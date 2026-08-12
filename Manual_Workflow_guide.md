# Odoo Multi-Agent Retail Automation Setup & Workflow Guide

This guide describes the complete Odoo configuration and transaction workflow required before running the automated retail system.

The automation assumes the following architecture:

* A camera detects when a customer smiles.
* The customer is treated as an anonymous **Walk-In Customer**.
* A predefined product such as **Lemonade** or **Bier** is sold.
* Odoo checks available stock.
* If stock is insufficient, the system automatically creates a Purchase Order and receives new stock.
* A Sales Order is created and confirmed.
* The product is delivered from warehouse stock.
* A customer invoice is created and posted.
* The system is then ready for the next customer.

---

# Part 1: Initial Odoo Configuration

These steps only need to be completed once when preparing a new Odoo installation for the automation.

---

## Step 1: Install Required Odoo Applications

The automation uses Sales, Inventory, Purchase, and Accounting functionality.

1. Open the Odoo **Apps** menu.
2. Search for and install:

   * **Sales**
   * **Inventory**
   * **Purchase**
   * **Invoicing / Accounting**
3. Wait until all required modules have been installed successfully.

> **System Requirement:**
>
> The Odoo user used by the Python automation must have permission to create and modify:
>
> * Sales Orders
> * Purchase Orders
> * Stock Transfers
> * Products
> * Customer Invoices

---

## Step 2: Configure the Main Warehouse

The automation should initially use one main warehouse with a simple stock flow.

1. Open the top-left app menu.
2. Navigate to **Inventory → Configuration → Warehouses**.
3. Open the existing warehouse or create a new warehouse.
4. Configure the warehouse name, for example:

   * **Main Warehouse**
   * Short Name: `WH`
5. Configure incoming shipments to use a simple direct receipt flow.
6. Configure outgoing shipments to use a simple direct delivery flow.
7. Multi-Step Routes Setting: If you do not see the options to select 1-step, 2-step, or 3-step routing on the Warehouse form, multi-step routes might be disabled in your global settings:

        * Go to Inventory → Configuration → Settings.
        * Under the Warehouse section, check the box for Multi-Step Routes.
        * Click Save.
        * Return to Inventory → Configuration → Warehouses and open your warehouse—the shipment flow options will now be visible.
8. Save the warehouse.

The desired stock movement should be:

```text
Vendor
   ↓
WH/Stock
   ↓
Customer
```

Avoid using an inter-warehouse transit location unless multiple warehouses are intentionally being used.

> **Important:**
>
> For the initial ERP automation, use a single warehouse wherever possible.
>
> The product should normally be stored in:
>
> `WH/Stock`
>
> and not:
>
> `Physical Locations/Inter-warehouse transit`

---

## Step 3: Identify the Warehouse ID

The Python application requires the Odoo database ID of the warehouse.

1. Enable **Developer Mode** in Odoo.
2. Navigate to **Inventory → Configuration → Warehouses**.
3. Open the warehouse used by the automation.
4. Inspect the browser URL.
5. Identify the numeric warehouse record ID.

Example:

```text
stock.warehouse ID = 1
```

Record this value.

It will later be added to the Python `.env` configuration:

```env
ODOO_WAREHOUSE_ID=1
```

> **Important:**
>
> Do not automatically assume the warehouse ID is `1`.
>
> Always verify the actual ID in the Odoo database.

---

## Step 4: Create the Walk-In Customer

The camera detects a smile but does not identify the person's identity.

Therefore, all camera-triggered transactions should use one generic Odoo customer.

1. Open the top-left app menu.
2. Navigate to **Contacts**.
3. Click **New**.
4. Enter:

```text
Name: Walk-In Customer
```

5. Save the contact.

Each customer transaction will use the same contact record.

Example:

```text
Customer A smiles
→ SO00001
→ Walk-In Customer

Customer B smiles
→ SO00002
→ Walk-In Customer

Customer C smiles
→ SO00003
→ Walk-In Customer
```

Each sale remains a separate Sales Order even though the same generic customer contact is used.

---

## Step 5: Identify the Walk-In Customer ID

The Python automation requires the database ID of the Walk-In Customer.

1. Keep **Developer Mode** enabled.
2. Navigate to **Contacts → Walk-In Customer**.
3. Open the customer record.
4. Inspect the URL.
5. Identify the numeric ID.

Example:

```text
Walk-In Customer
ID = 10
```

Record this value.

It will later be added to the `.env` file:

```env
ODOO_WALK_IN_CUSTOMER_ID=10
```

---

## Step 6: Create the Vendor

The automated restocking workflow requires a supplier.

1. Navigate to **Contacts** or **Purchase → Vendors**.
2. Click **New**.
3. Create the supplier.

Example:

```text
Vendor Name: Beverage Supplier
```

4. Save the vendor.

The vendor will later be linked directly to the product.

---

## Step 7: Create or Select the Product

1. Open the top-left app menu.
2. Navigate to **Inventory → Products → Products**.
3. Click **New**, or open the existing product.
4. Enter the desired product name.

Example:

```text
Product Name: Lemonade
```

or:

```text
Product Name: Bier
```

5. Configure the product so it can be both:

   * Sold
   * Purchased
6. Enable inventory tracking for the product means storable       product in Product type.
7. Save the product.

The product should support physical inventory quantities such as:

```text
On Hand
Forecasted
Incoming
Outgoing
```

---

## Step 8: Configure the Product Sales Price

The product should have a valid selling price.

1. Open the product.
2. Locate the **Sales Price** field.
3. Enter the selling price.

Example:

```text
Sales Price: €2.50
```

4. Save the product.

This price will be used when Sales Orders are created automatically.

---

## Step 9: Product & Vendor Configuration

Before automated purchasing can occur, the product must be mapped to an authorized vendor.

1. Navigate to **Inventory → Products → Products**.
2. Open the designated product, for example **Lemonade**.
3. Select the **Purchase** tab.
4. Locate the **Vendors** table.
5. Click **Add a line**.
6. Select the vendor created earlier.
7. Configure the purchase price.

Example:

```text
Vendor: Beverage Supplier
Price: €1.00
Minimum Quantity: 1
```

8. Save the product.

> **Required Result:**
>
> The product must have at least one valid vendor before automatic purchasing can succeed.

---

## Step 10: Configure Product Routes & Procurement Settings

To support a simple direct flow (**Vendor → WH/Stock → Customer**) where custom Python automation manages inventory replenishment, configure the product as follows:

---

### 1. Product Inventory Tab (Primary Route Selection)

1. Open **Inventory → Products → Products** and select your product.
2. Navigate to the **Inventory** tab.
3. Locate the **Routes** section and configure the checkboxes:
   * **`Buy` (Checked):** Allows the product to be purchased from a vendor and received into `WH/Stock`. Required for automated Purchase Order creation.
   * **`Replenish on Order (MTO)` (Unchecked):** Must be **OFF**. Enabling MTO causes Odoo to auto-generate procurements upon Sales Order confirmation, bypassing external logic.
   * **Inter-Warehouse / Resupply Routes (Unchecked):** Ensure options such as *Resupply Subcontractor*, *Transit*, or multi-warehouse resupply routes are **OFF** to avoid routing stock through transit locations.

---

### 2. Product Purchase Tab (Vendor Assignment)

For automated Purchase Order generation to link cleanly to a vendor:

1. Click the **Purchase** tab on the product form.
2. Under the **Vendors** section, click **Add a line**.
3. Select the default **Vendor**, set the **Price**, and specify any minimum order quantity.

---

### 3. Reordering Rules (Automated Stock Rules)

To prevent Odoo’s built-in scheduler from conflicting with external Python automation:

1. Click the **Reordering Rules** (or **Min/Max**) smart button at the top of the product card.
2. **If Python fully controls stock checks and PO creation:**
   * Leave this section empty (**0 rules**).
3. **If a reordering rule is required:**
   * Ensure the **Location** is explicitly set to `WH/Stock`.
   * Set the **Route** strictly to `Buy` (avoiding any transit or inter-warehouse routes).

---

## Step 11: Configure the Invoicing Policy

The recommended workflow creates an invoice after the physical delivery has completed.

1. Open the product.
2. Navigate to its General configuration.
3. Find the **Invoicing Policy**.
4. Select:

```text
Delivered Quantities
```

5. Save the product.

The resulting transaction flow becomes:

```text
Sales Order
    ↓
Delivery
    ↓
Invoice
```

---

## Step 12: Identify the Product Template ID

The Python application requires the database ID of the configured product template.

1. Enable **Developer Mode**.
2. Navigate to **Inventory → Products → Products**.
3. Open the product.
4. Inspect the browser URL.
5. Identify the product template record ID.

Example:

```text
Product: Lemonade
product.template ID = 15
```

Record this value.

The `.env` configuration would contain:

```env
ODOO_PRODUCT_TEMPLATE_ID=15
```

If the actual product ID is different, use the actual value.

For example:

```env
ODOO_PRODUCT_TEMPLATE_ID=37
```

Do not use `15` unless product template `15` really represents the required product.

---

## Step 13: Understand Product Template vs Product Variant

Odoo internally uses both:

```text
product.template
```

and:

```text
product.product
```

For example:

```text
Lemonade

product.template ID = 12
product.product ID  = 17
```

This is normal.

The Python connector resolves the configured product template into the corresponding product variant before creating Sales Order and Purchase Order lines.

The main configuration value therefore remains:

```env
ODOO_PRODUCT_TEMPLATE_ID=12
```

---

## Step 14: Configure Dedicated Automation User in Odoo

To allow the Python application to interact with Odoo via API, create a dedicated user with specific security permissions and API access.

---

### 1. Create the Automation User

1. Navigate to **Settings → Users & Companies → Users**.
2. Click **New**.
3. Configure the general credentials:
   * **Name:** `ai_agent_user@local`
   * **Login (Email):** `ai_agent_user@local`

---

### 2. Grant Access Rights

Assign the necessary permissions so the script can execute sales, procurement, inventory, and accounting operations:

* **Sales:** `Administrator` or `User: All Documents`
* **Inventory:** `Administrator` or `User`
* **Purchase:** `Administrator` or `User`
* **Accounting / Invoicing:** `Invoicing` or `Accountant`

---

### 3. Save the User

* Click the **Save** icon (cloud icon or **Save** button) at the top of the form view.

---

### 4. Set the User Password

After saving the user:

1. Click the **Action** menu (⚙️ gear icon or **Action** button) located at the top center of the user form.
2. Select **Change Password** from the dropdown menu.
3. In the pop-up window, enter a strong password in the **New Password** field.
   > **Important:** Do **not** use special characters (such as `@`, `#`, `$`, `%`, `&`, `!`, `?`, etc.) in the password, as these can cause URL-encoding issues or XML-RPC parsing errors in external Python integration scripts. Use only uppercase letters, lowercase letters, and digits (e.g., `ERPBarAutomation2026`).
4. Click **Change Password** to apply.

---

## API Capabilities Enabled

With these settings, the `ERP BAR Automation` user can perform the following XML-RPC / JSON-RPC calls:

* Create & Confirm Sales Orders
* Create & Confirm Purchase Orders
* Read stock levels & update inventory
* Validate Stock Transfers / Picking operations
* Create & Post Customer Invoices / Vendor Bills
---

## Step 15: Configure the Python Environment Variables

After gathering the Odoo configuration values, create or update the project's `.env` file.

Example:

```env
ODOO_URL=http://localhost:8069
ODOO_DB=erp_bar
ODOO_USER=ai_agent_user@local
ODOO_PASSWORD=your_password

ODOO_WALK_IN_CUSTOMER_ID=10
ODOO_WAREHOUSE_ID=1
ODOO_PRODUCT_TEMPLATE_ID=15
ODOO_RESTOCK_QTY=3
```

Each ID must correspond to the actual record in the Odoo database.

---

# Part 2: Manual Workflow Validation

Before running the Python automation, perform the complete workflow manually once.

This confirms that the Odoo database itself is configured correctly.

---

## Step 16: Check Current Stock

1. Navigate to **Inventory → Products → Products**.
2. Open **Lemonade**.
3. Read the **On Hand** quantity.

Example:

```text
On Hand: 0.00 Units
```

If the stock is zero, the restocking workflow can be tested.

---

## Step 17: Create a Manual Purchase Order

1. Open the top-left app menu.
2. Select **Purchase**.
3. Click **New**.
4. Select the configured vendor.

Example:

```text
Vendor: Beverage Supplier
```

5. Under **Products**, click **Add a product**.
6. Select **Lemonade**.
7. Set:

```text
Quantity: 20
```

8. Click **Confirm Order**.

> **System Response:**
>
> * The document changes from **RFQ** to **Purchase Order**.
> * A **Receipt** smart-button appears.
> * Odoo creates an incoming warehouse transfer.

---

## Step 18: Receive the Purchased Stock

1. Open the Purchase Order.
2. Click the **Receipt** smart-button.
3. Verify that the destination is the main warehouse stock location.

Expected destination:

```text
WH/Stock
```

4. Set the received quantity to:

```text
20
```

5. Click **Validate**.

> **Expected Result:**
>
> The receipt status changes to **Done**.

Return to:

**Inventory → Products → Lemonade**

The expected stock is:

```text
On Hand: 20.00 Units
```

---

## Step 19: Create a Manual Sales Order

1. Open the top-left app menu.
2. Select **Sales**.
3. Click **New**.
4. Select:

```text
Customer: Walk-In Customer
```

5. Under **Order Lines**, click **Add a product**.
6. Select:

```text
Product: Lemonade
Quantity: 1
```

7. Confirm that the correct warehouse is being used.
8. Click **Confirm**.

> **System Response:**
>
> * Status changes from **Quotation** to **Sales Order**.
> * A **Delivery** smart-button appears.
> * Odoo creates an outgoing transfer such as:
>
> `WH/OUT/xxxxx`

---

## Step 20: Validate the Delivery

1. Open the confirmed Sales Order.
2. Click the **Delivery** smart-button.
3. Confirm that the stock movement is:

```text
WH/Stock
   ↓
Customer
```

4. Verify:

```text
Product: Lemonade
Quantity: 1
```

5. Set the completed quantity to `1`.
6. Click **Validate**.

> **System Response:**
>
> * The Delivery status changes to **Done**.
> * Physical inventory decreases.

Expected stock:

```text
Before Delivery: 20
Delivered:         1
Remaining:        19
```

---

## Step 21: Create and Post the Invoice

1. Return to the Sales Order.
2. Click **Create Invoice**.
3. Create the regular invoice.
4. Open the invoice.
5. Review the product, quantity, customer, and amount.
6. Click **Confirm / Post**.

> **Expected Result:**
>
> Invoice status becomes:
>
> `Posted`

The complete manual transaction is now successful.

---

# Part 3: Automated Multi-Agent Runtime Workflow

After all manual tests succeed, the Python automation can be started.

---

## Step 22: Customer Smile Detection

A customer approaches the camera.

The MediaPipe camera system continuously evaluates facial expressions.

When the configured smile threshold is exceeded:

```text
Customer smiles
      ↓
Smile detected
      ↓
Automation triggered
```

The customer's identity is not determined.

The system automatically associates the transaction with:

```text
Walk-In Customer
```

---

## Step 23: Supervisor Agent Starts the Transaction

The Supervisor Agent receives the trigger and loads the configured:

```text
Product
Walk-In Customer
Warehouse
Required Quantity
```

Example:

```text
Product Template ID: 12
Quantity: 1
Customer: Walk-In Customer
Warehouse: Main Warehouse
```

The system should not dynamically guess these IDs.

They come from the application's configuration.

---

## Step 24: Inventory Agent Checks Current Stock

Before creating the Sales Order, the Inventory Agent checks available stock.

Example:

```text
Required Quantity: 1
Available Stock: 0
```

Decision:

```text
Available >= Required
        ↓
Continue directly to sale
```

or:

```text
Available < Required
        ↓
Start automatic procurement
```

---

## Step 25: Automatic Restock — Purchase Order

If stock is insufficient, the system reads the vendor assigned to the product.

The automation creates a Purchase Order automatically.

Example:

```text
Vendor: Beverage Supplier
Product: Lemonade
Quantity: 20
```

Odoo then:

```text
Creates RFQ
    ↓
Confirms Purchase Order
    ↓
Creates Incoming Receipt
```

---

## Step 26: Automatic Receipt Validation

The Inventory Agent locates the incoming stock transfer created by the Purchase Order.

Example:

```text
WH/IN/00015
```

The automation:

1. Finds the receipt.
2. Sets the received quantity.
3. Validates the receipt.
4. Confirms that the transfer reaches **Done**.

Expected inventory:

```text
Before Receipt: 0
Received:      20
On Hand:       20
```

---

## Step 27: Verify Stock After Replenishment

The Inventory Agent checks the product stock again.

Expected:

```text
Required: 1
Available: 20
```

The workflow continues only if:

```text
Available Stock >= Required Quantity
```

If the stock is still insufficient, the transaction must stop and return an error rather than creating an unfulfillable sale.

---

## Step 28: Sales Agent Creates the Sales Order

Once sufficient stock exists, the Sales Agent creates a new Sales Order.

The automated order contains:

```text
Customer: Walk-In Customer
Product: Lemonade
Quantity: 1
Warehouse: Main Warehouse
```

The Sales Agent then confirms the order.

> **System Response:**
>
> * A new Sales Order ID is generated.
> * Status changes to **Sales Order**.
> * Odoo creates an outgoing Delivery transfer.

Example:

```text
SO ID: 42
SO Name: S00042
```

---

## Step 29: Dispenser Agent Executes Delivery

The Inventory / Dispenser Agent locates the Delivery associated with the new Sales Order.

Example:

```text
WH/OUT/00042
```

It verifies:

```text
Source: WH/Stock
Destination: Customers
Product: Lemonade
Quantity: 1
```

The automation then:

1. Reserves the stock if necessary.
2. Sets the completed quantity to `1`.
3. Validates the transfer.

> **System Response:**
>
> Delivery status becomes:
>
> `Done`

Inventory changes from:

```text
20 → 19
```

---

## Step 30: Sales Agent Creates the Invoice

After delivery is successfully completed, the Sales Agent creates the customer invoice from the Sales Order.

The automation:

```text
Creates Invoice
      ↓
Posts Invoice
```

Expected result:

```text
Invoice Status: Posted
```

---

## Step 31: Transaction Completion

The system records the final transaction result.

A successful transaction should contain identifiers such as:

```text
Product: Lemonade
Customer: Walk-In Customer

Sales Order:
S00042

Purchase Order:
P00015
(if restocking was required)

Receipt:
WH/IN/00015
(if restocking was required)

Delivery:
WH/OUT/00042

Invoice:
INV/2026/00042

Status:
SUCCESS
```

The system is then ready for the next customer.

---

# Part 4: Multiple Customer Behavior

The system does not need to create a new Odoo customer contact every time someone approaches the camera.

Instead:

```text
Customer A smiles
      ↓
Transaction #1
      ↓
Walk-In Customer
      ↓
S00042
```

Then:

```text
Customer B arrives
      ↓
Smiles
      ↓
Transaction #2
      ↓
Walk-In Customer
      ↓
S00043
```

Then:

```text
Customer C arrives
      ↓
Smiles
      ↓
Transaction #3
      ↓
Walk-In Customer
      ↓
S00044
```

The Sales Orders, deliveries, invoices, and timestamps distinguish the transactions.

---

# Part 5: Complete Automation Sequence

The complete ERP automation flow is:

```text
Customer approaches camera
          ↓
Smile detected
          ↓
Supervisor Agent triggered
          ↓
Load configured Product
          ↓
Load Walk-In Customer
          ↓
Load Main Warehouse
          ↓
Check Product Stock
          ↓
     Is stock enough?
       /        \
     YES        NO
      |          |
      |     Find Product Vendor
      |          ↓
      |     Create PO ×20
      |          ↓
      |     Confirm PO
      |          ↓
      |     Find Receipt
      |          ↓
      |     Receive Stock
      |          ↓
      |     Validate Receipt
      |          ↓
      |     Verify Stock
      |          |
      └──────────┘
           ↓
     Create Sales Order
           ↓
     Confirm Sales Order
           ↓
       Find Delivery
           ↓
     Set Delivered Qty
           ↓
     Validate Delivery
           ↓
      Verify Delivery
           ↓
       Create Invoice
           ↓
        Post Invoice
           ↓
     Transaction SUCCESS
           ↓
     Ready for Next Customer
```

---

# Part 6: Pre-Run Checklist

Before running the Python automation, verify every item below.

### Odoo Applications

```text
[ ] Sales installed
[ ] Inventory installed
[ ] Purchase installed
[ ] Accounting / Invoicing installed
```

### Warehouse

```text
[ ] Main Warehouse exists
[ ] Correct warehouse ID recorded
[ ] Receipt sends products to WH/Stock
[ ] Delivery sends products from WH/Stock
[ ] No unintended inter-warehouse route
```

### Product

```text
[ ] Product exists
[ ] Product can be sold
[ ] Product can be purchased
[ ] Inventory tracking enabled
[ ] Sales price configured
[ ] Product Template ID recorded
```

### Vendor

```text
[ ] Vendor exists
[ ] Vendor assigned to product
[ ] Purchase price configured
```

### Customer

```text
[ ] Walk-In Customer exists
[ ] Walk-In Customer ID recorded
```

### Purchasing

```text
[ ] Manual Purchase Order succeeds
[ ] Receipt is generated
[ ] Receipt can be validated
[ ] Stock reaches WH/Stock
```

### Sales

```text
[ ] Manual Sales Order succeeds
[ ] Correct warehouse is used
[ ] Delivery is generated
[ ] Delivery can be validated
[ ] Inventory decreases correctly
```

### Accounting

```text
[ ] Invoice can be created
[ ] Invoice can be posted
```

### Automation User

```text
[ ] Odoo automation user exists
[ ] Sales permission available
[ ] Purchase permission available
[ ] Inventory permission available
[ ] Accounting permission available
```

### Python Configuration

```text
[ ] ODOO_URL correct
[ ] ODOO_DB correct
[ ] ODOO_USER correct
[ ] ODOO_PASSWORD correct

[ ] ODOO_PRODUCT_TEMPLATE_ID correct
[ ] ODOO_WALK_IN_CUSTOMER_ID correct
[ ] ODOO_WAREHOUSE_ID correct
[ ] ODOO_RESTOCK_QTY correct
```

---

# Part 7: Recommended First Test

Before running the camera system, run the Odoo integration test:

```bash
python test_odoo_integration.py
```

The expected execution order is:

```text
Odoo Authentication
        ↓
Validate Configuration
        ↓
Check Product Stock
        ↓
Restock if Required
        ↓
Create Sales Order
        ↓
Confirm Sales Order
        ↓
Validate Delivery
        ↓
Create Invoice
        ↓
Post Invoice
        ↓
SUCCESS
```

Only after this test completes successfully should the camera-based application be started.

---

# Part 8: Start the Retail Automation

Launch the camera application:

```bash
python smile_detector_llm_hud.py
```

The system should then be ready for live operation:

```text
Camera Active
    ↓
Customer Smiles
    ↓
Automatic Odoo Transaction
    ↓
Product Delivered
    ↓
Invoice Posted
    ↓
System Ready for Next Customer
```

This completes the Odoo Multi-Agent Retail Automation setup and operational workflow.
