from odoo_connector import (
    OdooBridge,
    DEFAULT_PRODUCT_TEMPLATE_ID,
    DEFAULT_RESTOCK_QTY,
)


def test_odoo_connection():
    print("--- 1. Testing Connection / Configuration ---")
    bridge = OdooBridge()
    print(f"Connected successfully. UID: {bridge.uid}")

    config = bridge.validate_runtime_configuration(DEFAULT_PRODUCT_TEMPLATE_ID)
    print(f"Configuration: {config}")

    print("\n--- 2. Testing Stock Query ---")
    initial_stock = bridge.check_product_stock(DEFAULT_PRODUCT_TEMPLATE_ID)
    print(f"Initial Stock: {initial_stock} units")

    if initial_stock < 1:
        print("\n--- 3. Testing Auto Purchase & Receipt ---")
        po_result = bridge.trigger_auto_purchase_order(
            DEFAULT_PRODUCT_TEMPLATE_ID,
            qty=DEFAULT_RESTOCK_QTY,
        )
        print(f"Auto-Purchase Result: {po_result}")
        if po_result.get("status") != "SUCCESS":
            raise RuntimeError(po_result)

    print("\n--- 4. Testing Sales Order for Generic Walk-In Customer ---")
    so_result = bridge.create_sale_order(
        product_template_id=DEFAULT_PRODUCT_TEMPLATE_ID,
        qty=1,
    )
    print(f"Sale Order: {so_result}")

    print("\n--- 5. Testing Delivery Validation ---")
    delivery_result = bridge.validate_delivery_order(so_result["so_id"])
    print(f"Delivery: {delivery_result}")
    if delivery_result.get("status") != "DELIVERED":
        raise RuntimeError(delivery_result)

    print("\n--- 6. Testing Invoice Creation/Post ---")
    invoice_result = bridge.create_and_post_invoice(so_result["so_id"])
    print(f"Invoice: {invoice_result}")

    print("\n✅ Full Odoo workflow executed successfully!")


if __name__ == "__main__":
    test_odoo_connection()
