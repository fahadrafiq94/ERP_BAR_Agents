import json
import queue
import threading
from typing import Optional

from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage

from odoo_connector import DEFAULT_PRODUCT_TEMPLATE_ID, DEFAULT_RESTOCK_QTY
from odoo_tools import (
    validate_configuration_tool,
    check_stock_tool,
    auto_purchase_stock_tool,
    create_sale_order_tool,
    validate_delivery_tool,
    create_invoice_tool,
)

# Shared queue consumed by the OpenCV HUD.
thought_queue = queue.Queue()
MODEL_NAME = "qwen2.5:1.5b"


def get_narration_llm():
    """Small local model used only to narrate visible agent decisions.

    IMPORTANT: this model does not control Odoo IDs or workflow execution.
    Python remains the source of truth for every transaction identifier.
    """
    return ChatOllama(model=MODEL_NAME, temperature=0.2)


def _invoke_json(tool_obj, args: dict) -> dict:
    """Invoke a LangChain tool directly and require a JSON object result."""
    output = tool_obj.invoke(args)
    if isinstance(output, dict):
        return output
    if not isinstance(output, str):
        raise RuntimeError(f"Unexpected tool result type: {type(output).__name__}")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Tool returned invalid JSON: {output}") from exc


def _require_status(result: dict, accepted: set[str], step: str) -> None:
    status = str(result.get("status", "")).upper()
    if status not in accepted:
        reason = result.get("reason") or result
        raise RuntimeError(f"{step} failed: {reason}")


class VisibleAgentNarrator:
    """Generates short audit-style reasoning summaries for the HUD.

    These are intentionally concise, visible explanations of decisions based on
    known state. They are not used to execute the transaction and cannot alter
    product, customer, warehouse, Sale Order, picking, or invoice IDs.
    """

    def __init__(self):
        self.llm = get_narration_llm()

    def say(self, agent: str, event: str, facts: str, next_action: str) -> None:
        system = (
            f"You are the {agent} in a retail ERP demonstration. "
            "Write ONE short first-person decision narration for a live HUD. "
            "Explain what you observed and why the stated next action follows. "
            "Use only the supplied facts. Do not invent IDs, quantities, states, or actions. "
            "Maximum 24 words. No bullets, no preamble, no quotation marks."
        )
        human = (
            f"EVENT: {event}\n"
            f"FACTS: {facts}\n"
            f"NEXT ACTION: {next_action}"
        )
        try:
            response = self.llm.invoke([
                SystemMessage(content=system),
                HumanMessage(content=human),
            ])
            text = str(response.content).strip().replace("\n", " ")
            if text:
                thought_queue.put(f"🧠 [{agent} THINK] {text}")
        except Exception as exc:
            # Narration is presentation-only. Never fail an Odoo transaction
            # because the local LLM was unavailable.
            thought_queue.put(f"⚠️ [{agent} NARRATION] unavailable: {exc}")


class AgentReasoningRunner:
    """Runs the Odoo transaction deterministically with LLM-narrated decisions."""

    def __init__(self):
        self._run_lock = threading.Lock()
        self.narrator = VisibleAgentNarrator()

    def _run_transaction(self, trigger_context: str) -> dict:
        product_template_id = DEFAULT_PRODUCT_TEMPLATE_ID
        required_qty = 1
        restock_qty = DEFAULT_RESTOCK_QTY

        # ------------------------------------------------------------------
        # SUPERVISOR: understand trigger and validate fixed configuration.
        # ------------------------------------------------------------------
        self.narrator.say(
            "SUPERVISOR",
            "A smile event triggered a retail transaction.",
            trigger_context,
            "Validate the configured product, walk-in customer, and warehouse before delegating work.",
        )
        thought_queue.put("👑 [SUPERVISOR → CONFIG] Validating Odoo configuration...")

        config = _invoke_json(
            validate_configuration_tool,
            {"product_template_id": product_template_id},
        )
        _require_status(config, {"SUCCESS"}, "Configuration validation")
        thought_queue.put(
            f"👁 [OBSERVATION] Product={config.get('product_name')} | "
            f"Customer={config.get('walk_in_customer_name')} | "
            f"Warehouse={config.get('warehouse_name')}"
        )

        # ------------------------------------------------------------------
        # INVENTORY AGENT: check and optionally replenish stock.
        # ------------------------------------------------------------------
        self.narrator.say(
            "INVENTORY AGENT",
            "The supervisor delegated inventory readiness.",
            f"Product={config.get('product_name')}; required quantity={required_qty}.",
            "Check on-hand stock before allowing the sale to proceed.",
        )
        thought_queue.put("📦 [INVENTORY AGENT → TOOL] Checking on-hand stock...")

        stock_result = _invoke_json(
            check_stock_tool,
            {"product_template_id": product_template_id},
        )
        _require_status(stock_result, {"SUCCESS"}, "Stock check")
        stock_before = float(stock_result.get("stock", 0.0))
        purchase_result: Optional[dict] = None
        thought_queue.put(
            f"👁 [OBSERVATION] Available stock={stock_before}; required={required_qty}."
        )

        if stock_before < required_qty:
            self.narrator.say(
                "INVENTORY AGENT",
                "Stock is below the required quantity.",
                f"Available={stock_before}; required={required_qty}; configured restock={restock_qty}.",
                "Create and receive a purchase order, then verify stock again.",
            )
            thought_queue.put(
                f"🛒 [INVENTORY AGENT → PURCHASE] Restocking {restock_qty} units..."
            )
            purchase_result = _invoke_json(
                auto_purchase_stock_tool,
                {
                    "product_template_id": product_template_id,
                    "qty": restock_qty,
                },
            )
            _require_status(purchase_result, {"SUCCESS"}, "Automatic purchase/receipt")
            thought_queue.put(
                f"✅ [PURCHASE RESULT] PO={purchase_result.get('po_name', purchase_result.get('po_id'))}"
            )

            verify_stock = _invoke_json(
                check_stock_tool,
                {"product_template_id": product_template_id},
            )
            _require_status(verify_stock, {"SUCCESS"}, "Post-purchase stock verification")
            stock_after = float(verify_stock.get("stock", 0.0))
            thought_queue.put(f"👁 [OBSERVATION] Stock after receipt={stock_after}.")
            if stock_after < required_qty:
                raise RuntimeError(
                    f"Restock completed but only {stock_after} units are available; "
                    f"{required_qty} required."
                )
            self.narrator.say(
                "INVENTORY AGENT",
                "Replenishment and receipt completed.",
                f"Available stock is now {stock_after}; required quantity is {required_qty}.",
                "Return control to the supervisor because inventory is ready for sale.",
            )
        else:
            self.narrator.say(
                "INVENTORY AGENT",
                "Stock check completed.",
                f"Available={stock_before}; required={required_qty}.",
                "Skip purchasing and return control to the supervisor because stock is sufficient.",
            )

        # ------------------------------------------------------------------
        # SUPERVISOR -> SALES AGENT.
        # ------------------------------------------------------------------
        self.narrator.say(
            "SUPERVISOR",
            "Inventory reported that the product is ready for fulfillment.",
            f"Product={config.get('product_name')}; quantity={required_qty}; customer={config.get('walk_in_customer_name')}.",
            "Activate the Sales Agent to create and confirm the walk-in Sales Order.",
        )
        thought_queue.put("👑 [SUPERVISOR → SALES AGENT] Delegating Sales Order creation...")

        self.narrator.say(
            "SALES AGENT",
            "The supervisor requested a walk-in sale.",
            f"Customer={config.get('walk_in_customer_name')}; product={config.get('product_name')}; quantity={required_qty}.",
            "Create and confirm the Sales Order, then return its exact Odoo ID.",
        )
        thought_queue.put("💼 [SALES AGENT → TOOL] Creating and confirming Sales Order...")

        sale = _invoke_json(
            create_sale_order_tool,
            {
                "product_template_id": product_template_id,
                "qty": required_qty,
            },
        )
        _require_status(sale, {"CONFIRMED", "SALE", "SUCCESS"}, "Sales Order creation")

        so_id = sale.get("so_id")
        if not isinstance(so_id, int) or so_id <= 0:
            raise RuntimeError(f"Sales Order returned invalid so_id: {so_id!r}")
        so_name = sale.get("so_name", str(so_id))
        thought_queue.put(f"✅ [SALES RESULT] Created {so_name}; exact Odoo ID={so_id}.")

        # ------------------------------------------------------------------
        # SUPERVISOR -> DISPENSER/DELIVERY AGENT.
        # ------------------------------------------------------------------
        self.narrator.say(
            "SUPERVISOR",
            "The Sales Agent confirmed the order.",
            f"Sales Order={so_name}; exact Odoo ID={so_id}.",
            "Activate the Dispenser Agent and validate the delivery using this exact order ID.",
        )
        thought_queue.put("👑 [SUPERVISOR → DISPENSER AGENT] Delegating physical delivery...")

        self.narrator.say(
            "DISPENSER AGENT",
            "A confirmed Sales Order is ready for fulfillment.",
            f"Sales Order={so_name}; quantity={required_qty}.",
            "Find its outgoing picking, set the completed quantity, and validate delivery.",
        )
        thought_queue.put(f"🚚 [DISPENSER AGENT → TOOL] Delivering {so_name}...")

        delivery = _invoke_json(
            validate_delivery_tool,
            {"sale_order_id": so_id},
        )
        _require_status(delivery, {"DELIVERED", "DONE"}, "Delivery validation")
        picking_name = delivery.get("picking_name", delivery.get("picking_id"))
        thought_queue.put(f"✅ [DELIVERY RESULT] {picking_name} is Done.")

        # ------------------------------------------------------------------
        # SUPERVISOR -> INVOICE AGENT.
        # ------------------------------------------------------------------
        self.narrator.say(
            "SUPERVISOR",
            "The Dispenser Agent completed delivery.",
            f"Sales Order={so_name}; delivery={picking_name}; delivery status=Done.",
            "Activate the Invoice Agent because delivered quantities can now be invoiced.",
        )
        thought_queue.put("👑 [SUPERVISOR → INVOICE AGENT] Delegating invoice creation...")

        self.narrator.say(
            "INVOICE AGENT",
            "The sale has been physically delivered.",
            f"Sales Order={so_name}; exact Odoo ID={so_id}.",
            "Create the customer invoice from the Sales Order and post it.",
        )
        thought_queue.put(f"🧾 [INVOICE AGENT → TOOL] Creating invoice for {so_name}...")

        invoice = _invoke_json(
            create_invoice_tool,
            {"sale_order_id": so_id},
        )
        _require_status(invoice, {"POSTED"}, "Invoice creation/posting")
        invoice_name = invoice.get("invoice_name", invoice.get("invoice_id"))
        thought_queue.put(f"✅ [INVOICE RESULT] {invoice_name} posted.")

        self.narrator.say(
            "SUPERVISOR",
            "All delegated agents completed successfully.",
            f"Sales Order={so_name}; delivery={picking_name}; invoice={invoice_name}.",
            "Close the transaction as successful and become ready for the next customer.",
        )

        return {
            "status": "SUCCESS",
            "product_template_id": product_template_id,
            "sale_order_id": so_id,
            "sale_order_name": so_name,
            "purchase": purchase_result,
            "delivery": delivery,
            "invoice": invoice,
        }

    def run_agent_async(self, prompt_text: str):
        """Start one retail transaction without blocking the camera loop."""

        def worker():
            if not self._run_lock.acquire(blocking=False):
                thought_queue.put("⚠️ [BUSY] Previous transaction still running; trigger ignored.")
                return

            thought_queue.put("🚀 [EVENT] Smile trigger accepted. Multi-agent workflow starting...")
            try:
                result = self._run_transaction(prompt_text)
                thought_queue.put(
                    "✅ [FINAL] "
                    f"SO={result['sale_order_name']} (ID {result['sale_order_id']}) | "
                    f"Delivery={result['delivery'].get('picking_name')} | "
                    f"Invoice={result['invoice'].get('invoice_name')}"
                )
            except Exception as exc:
                thought_queue.put(f"❌ [ERROR] {exc}")
            finally:
                self._run_lock.release()

        threading.Thread(target=worker, daemon=True).start()
