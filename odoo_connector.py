import os
import xmlrpc.client
from typing import Union
from dotenv import load_dotenv

load_dotenv()

# Product TEMPLATE requested by the project.
DEFAULT_PRODUCT_TEMPLATE_ID = int(os.getenv("ODOO_PRODUCT_TEMPLATE_ID", "12"))
DEFAULT_RESTOCK_QTY = int(os.getenv("ODOO_RESTOCK_QTY", "20"))


def _required_int_env(name: str) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            f"Configure it in .env before running the automation."
        )
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {value!r}") from exc


class OdooBridge:
    def __init__(self):
        self.url = os.getenv("ODOO_URL")
        self.db = os.getenv("ODOO_DB")
        self.username = os.getenv("ODOO_USER")
        self.password = os.getenv("ODOO_PASSWORD")

        missing = [
            name
            for name, value in {
                "ODOO_URL": self.url,
                "ODOO_DB": self.db,
                "ODOO_USER": self.username,
                "ODOO_PASSWORD": self.password,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing Odoo connection settings: {', '.join(missing)}")

        # These are deliberately configuration values, not LLM-selected values.
        self.walk_in_customer_id = _required_int_env("ODOO_WALK_IN_CUSTOMER_ID")
        self.warehouse_id = _required_int_env("ODOO_WAREHOUSE_ID")

        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        try:
            self.uid = self.common.authenticate(
                self.db, self.username, self.password, {}
            )
        except Exception as exc:
            raise ConnectionError(f"Odoo RPC Authentication Error: {exc}") from exc

        if not self.uid:
            raise PermissionError(
                "Odoo Authentication Failed. Verify environment variables."
            )

        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

    def execute(self, model: str, method: str, *args, **kwargs):
        return self.models.execute_kw(
            self.db,
            self.uid,
            self.password,
            model,
            method,
            list(args),
            kwargs if kwargs else {},
        )

    # ------------------------------------------------------------------
    # CONFIG / PRODUCT RESOLUTION
    # ------------------------------------------------------------------

    def get_variant_id(self, product_template_id: int) -> int:
        """Resolve a product.template ID to its product.product variant ID."""
        template_id = product_template_id or DEFAULT_PRODUCT_TEMPLATE_ID
        variant_ids = self.execute(
            "product.product",
            "search",
            [["product_tmpl_id", "=", template_id]],
            limit=1,
        )
        if not variant_ids:
            raise ValueError(
                f"No product.product variant found for product.template #{template_id}."
            )
        return int(variant_ids[0])

    def validate_runtime_configuration(self, product_template_id: int) -> dict:
        """Fail early if the configured product, customer, or warehouse does not exist."""
        variant_id = self.get_variant_id(product_template_id)

        customer = self.execute(
            "res.partner", "read", [self.walk_in_customer_id], fields=["name"]
        )
        if not customer:
            raise ValueError(
                f"Walk-in customer #{self.walk_in_customer_id} does not exist."
            )

        warehouse = self.execute(
            "stock.warehouse", "read", [self.warehouse_id], fields=["name", "code"]
        )
        if not warehouse:
            raise ValueError(f"Warehouse #{self.warehouse_id} does not exist.")

        product = self.execute(
            "product.product",
            "read",
            [variant_id],
            fields=["display_name", "qty_available", "product_tmpl_id"],
        )
        if not product:
            raise ValueError(f"Product variant #{variant_id} does not exist.")

        return {
            "status": "SUCCESS",
            "product_template_id": product_template_id,
            "product_variant_id": variant_id,
            "product_name": product[0]["display_name"],
            "walk_in_customer_id": self.walk_in_customer_id,
            "walk_in_customer_name": customer[0]["name"],
            "warehouse_id": self.warehouse_id,
            "warehouse_name": warehouse[0]["name"],
        }

    # ------------------------------------------------------------------
    # STOCK / PROCUREMENT
    # ------------------------------------------------------------------

    def check_product_stock(self, product_template_id: int) -> float:
        variant_id = self.get_variant_id(product_template_id)
        data = self.execute(
            "product.product", "read", [variant_id], fields=["qty_available"]
        )
        return float(data[0]["qty_available"]) if data else 0.0

    def trigger_auto_purchase_order(
        self, product_template_id: int, qty: int = DEFAULT_RESTOCK_QTY
    ) -> dict:
        """Create PO, confirm it, receive it, then verify stock."""
        variant_id = self.get_variant_id(product_template_id)

        product_data = self.execute(
            "product.product",
            "read",
            [variant_id],
            fields=["display_name", "seller_ids"],
        )
        if not product_data:
            return {"status": "FAILED", "reason": "Product variant not found."}

        seller_ids = product_data[0].get("seller_ids", [])
        if not seller_ids:
            return {
                "status": "FAILED",
                "reason": f"No vendor configured for {product_data[0]['display_name']}.",
            }

        supplier_info = self.execute(
            "product.supplierinfo",
            "read",
            [seller_ids[0]],
            fields=["partner_id", "price"],
        )
        if not supplier_info or not supplier_info[0].get("partner_id"):
            return {"status": "FAILED", "reason": "Vendor record is incomplete."}

        vendor_id = supplier_info[0]["partner_id"][0]
        unit_price = float(supplier_info[0].get("price") or 1.0)

        po_id = self.execute(
            "purchase.order",
            "create",
            [
                {
                    "partner_id": vendor_id,
                    "picking_type_id": self._warehouse_in_type_id(),
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": variant_id,
                                "product_qty": qty,
                                "price_unit": unit_price,
                            },
                        )
                    ],
                }
            ],
        )
        if isinstance(po_id, list):
            po_id = po_id[0]

        self.execute("purchase.order", "button_confirm", [po_id])
        po_data = self.execute(
            "purchase.order", "read", [po_id], fields=["name", "state", "picking_ids"]
        )[0]

        picking_ids = po_data.get("picking_ids", [])
        if not picking_ids:
            return {
                "status": "FAILED",
                "po_id": po_id,
                "po_name": po_data["name"],
                "reason": "PO confirmed but no incoming receipt was generated.",
            }

        receipt_id = picking_ids[0]
        self._set_picking_done_quantities(receipt_id)
        validate_result = self.execute("stock.picking", "button_validate", [receipt_id])

        # button_validate can return an action/wizard in some Odoo configurations.
        if isinstance(validate_result, dict) and validate_result.get("res_model"):
            return {
                "status": "FAILED",
                "po_id": po_id,
                "po_name": po_data["name"],
                "receipt_id": receipt_id,
                "reason": (
                    "Receipt validation opened an Odoo wizard. "
                    "Use a one-step receipt configuration or handle the wizard explicitly."
                ),
            }

        receipt = self.execute(
            "stock.picking", "read", [receipt_id], fields=["state", "name"]
        )[0]
        stock_after = self.check_product_stock(product_template_id)

        if receipt["state"] != "done":
            return {
                "status": "FAILED",
                "po_id": po_id,
                "po_name": po_data["name"],
                "receipt_id": receipt_id,
                "receipt_state": receipt["state"],
                "reason": "Incoming receipt was not completed.",
            }

        return {
            "status": "SUCCESS",
            "po_id": po_id,
            "po_name": po_data["name"],
            "receipt_id": receipt_id,
            "receipt_name": receipt["name"],
            "stock_after": stock_after,
        }

    def _warehouse_in_type_id(self) -> int:
        data = self.execute(
            "stock.warehouse",
            "read",
            [self.warehouse_id],
            fields=["in_type_id"],
        )[0]
        return data["in_type_id"][0]

    def _set_picking_done_quantities(self, picking_id: int) -> None:
        move_ids = self.execute(
            "stock.move", "search", [["picking_id", "=", picking_id]]
        )
        for move_id in move_ids:
            move = self.execute(
                "stock.move",
                "read",
                [move_id],
                fields=["product_uom_qty"],
            )[0]
            self.execute(
                "stock.move",
                "write",
                [move_id],
                {"quantity": move["product_uom_qty"]},
            )

    # ------------------------------------------------------------------
    # SALES / DELIVERY / INVOICE
    # ------------------------------------------------------------------

    def create_sale_order(self, product_template_id: int, qty: int = 1) -> dict:
        """Create and confirm a SO for the configured generic walk-in customer."""
        variant_id = self.get_variant_id(product_template_id)

        so_id = self.execute(
            "sale.order",
            "create",
            [
                {
                    "partner_id": self.walk_in_customer_id,
                    "warehouse_id": self.warehouse_id,
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": variant_id,
                                "product_uom_qty": qty,
                            },
                        )
                    ],
                }
            ],
        )
        if isinstance(so_id, list):
            so_id = so_id[0]

        self.execute("sale.order", "action_confirm", [so_id])
        so_data = self.execute(
            "sale.order",
            "read",
            [so_id],
            fields=["name", "state", "picking_ids"],
        )[0]

        return {
            "status": "CONFIRMED" if so_data["state"] == "sale" else so_data["state"],
            "so_id": so_id,
            "so_name": so_data["name"],
            "picking_ids": so_data.get("picking_ids", []),
            "partner_id": self.walk_in_customer_id,
            "warehouse_id": self.warehouse_id,
            "product_variant_id": variant_id,
        }

    def validate_delivery_order(self, sale_order_input: Union[int, str]) -> dict:
        if isinstance(sale_order_input, int):
            domain = [["sale_id", "=", sale_order_input], ["state", "!=", "cancel"]]
        else:
            domain = [["origin", "=", str(sale_order_input)], ["state", "!=", "cancel"]]

        picking_ids = self.execute("stock.picking", "search", domain, limit=1)
        if not picking_ids:
            return {
                "status": "FAILED",
                "reason": f"No active delivery picking found for {sale_order_input}",
            }

        picking_id = picking_ids[0]
        self.execute("stock.picking", "action_assign", [picking_id])
        self._set_picking_done_quantities(picking_id)
        validate_result = self.execute("stock.picking", "button_validate", [picking_id])

        if isinstance(validate_result, dict) and validate_result.get("res_model"):
            return {
                "status": "FAILED",
                "picking_id": picking_id,
                "reason": (
                    "Delivery validation opened an Odoo wizard. "
                    "Check picking quantities/backorder settings."
                ),
            }

        picking = self.execute(
            "stock.picking", "read", [picking_id], fields=["name", "state"]
        )[0]
        return {
            "status": "DELIVERED" if picking["state"] == "done" else picking["state"],
            "picking_id": picking_id,
            "picking_name": picking["name"],
            "sale_order": sale_order_input,
        }

    def create_and_confirm_invoice(self, sale_order_id: int) -> dict:
        """
        Create the customer invoice from a delivered Sales Order
        and confirm it. Nothing else is done afterward.
        """

        context = {
            "active_model": "sale.order",
            "active_ids": [sale_order_id],
            "active_id": sale_order_id,
        }

        # ---------------------------------------------------------
        # 1. Check whether this Sale Order already has an invoice
        # ---------------------------------------------------------
        so = self.execute(
            "sale.order",
            "read",
            [sale_order_id],
            fields=["name", "invoice_ids"],
        )[0]

        invoice_ids = so.get("invoice_ids", [])

        # ---------------------------------------------------------
        # 2. Create invoice if one does not exist
        # ---------------------------------------------------------
        if not invoice_ids:

            wizard_id = self.execute(
                "sale.advance.payment.inv",
                "create",
                [{
                    "advance_payment_method": "delivered"
                }],
                context=context,
            )

            if isinstance(wizard_id, list):
                wizard_id = wizard_id[0]

            self.execute(
                "sale.advance.payment.inv",
                "create_invoices",
                [wizard_id],
                context=context,
            )

            # Read SO once to obtain generated invoice ID
            so = self.execute(
                "sale.order",
                "read",
                [sale_order_id],
                fields=["name", "invoice_ids"],
            )[0]

            invoice_ids = so.get("invoice_ids", [])

        if not invoice_ids:
            return {
                "status": "FAILED",
                "reason": "Invoice could not be created.",
            }

        invoice_id = invoice_ids[-1]

        # ---------------------------------------------------------
        # 3. CONFIRM the invoice
        #
        # This is equivalent to clicking Confirm in Odoo.
        # ---------------------------------------------------------
        self.execute(
            "account.move",
            "action_post",
            [invoice_id],
        )

        # ---------------------------------------------------------
        # 4. STOP HERE
        #
        # Do NOT perform another read.
        # Do NOT query payment_state.
        # Do NOT do any additional RPC calls.
        # ---------------------------------------------------------

        return {
            "status": "CONFIRMED",
            "sale_order_id": sale_order_id,
            "sale_order_name": so["name"],
            "invoice_id": invoice_id,
        }
