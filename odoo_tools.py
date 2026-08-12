import json
from langchain_core.tools import tool
from odoo_connector import OdooBridge, DEFAULT_PRODUCT_TEMPLATE_ID, DEFAULT_RESTOCK_QTY

bridge = OdooBridge()


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


@tool
def validate_configuration_tool(
    product_template_id: int = DEFAULT_PRODUCT_TEMPLATE_ID,
) -> str:
    """Validate configured product, walk-in customer and warehouse before a transaction."""
    return _dump(bridge.validate_runtime_configuration(product_template_id))


@tool
def check_stock_tool(
    product_template_id: int = DEFAULT_PRODUCT_TEMPLATE_ID,
) -> str:
    """Return current physical stock for the configured product template."""
    stock = bridge.check_product_stock(product_template_id)
    return _dump({
        "status": "SUCCESS",
        "product_template_id": product_template_id,
        "stock": stock,
    })


@tool
def auto_purchase_stock_tool(
    product_template_id: int = DEFAULT_PRODUCT_TEMPLATE_ID,
    qty: int = DEFAULT_RESTOCK_QTY,
) -> str:
    """Purchase and receive stock for the configured product when stock is insufficient."""
    return _dump(bridge.trigger_auto_purchase_order(product_template_id, qty))


@tool
def create_sale_order_tool(
    product_template_id: int = DEFAULT_PRODUCT_TEMPLATE_ID,
    qty: int = 1,
) -> str:
    """Create and confirm a sale for the configured generic walk-in customer."""
    return _dump(bridge.create_sale_order(product_template_id, qty))


@tool
def validate_delivery_tool(sale_order_id: int) -> str:
    """Reserve, set done quantity, and validate the delivery for a sale order."""
    return _dump(bridge.validate_delivery_order(sale_order_id))


@tool
def create_invoice_tool(sale_order_id: int) -> str:
    """Create and post the customer invoice for a delivered sale order."""
    return _dump(bridge.create_and_confirm_invoice(sale_order_id))


tools = [
    validate_configuration_tool,
    check_stock_tool,
    auto_purchase_stock_tool,
    create_sale_order_tool,
    validate_delivery_tool,
    create_invoice_tool,
]
