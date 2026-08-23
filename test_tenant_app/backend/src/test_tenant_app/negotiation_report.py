"""Negotiation Report PDF — classification, strategy, quotation evolution, and
award reasoning behind a completed PurchaseOrder.

One requisition can span several categories (e.g. IT Hardware + Rare Earth
Components); the real orchestrator negotiates each category separately (its own
Kraljic classification, strategy, and supplier), so this report has one section
per category rather than one for the whole requisition.

Every item/category/supplier fact here is the tenant's own catalog data. The
per-round quotation amounts are illustrative — this demo has no live multi-round
bid feed — but they reconcile exactly to the requisition's budget and the issued
PO's total, so the story told is internally consistent with the documents it
accompanies.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from test_tenant_app.clients.skill_client import skill_client
from test_tenant_app.models import LineItem, PurchaseOrder, PurchaseRequisition

_STYLES = getSampleStyleSheet()
_HEADER_BG = colors.HexColor("#1f2937")
_ACCENT_BG = colors.HexColor("#eef2ff")
_ROW_ALT_BG = colors.HexColor("#f9fafb")
_GRID_COLOR = colors.HexColor("#d1d5db")


# Table cells wrap long text via Paragraph — a plain string in a reportlab Table
# never wraps and silently overflows into the next column instead.
def _cell(text: str, font_size: float = 8.5) -> Paragraph:
    style = ParagraphStyle(
        f"cell{font_size}", fontName="Helvetica", fontSize=font_size, leading=font_size + 2
    )
    return Paragraph(text, style)


# Kraljic quadrant -> (strategy label, one-line description). Mirrors
# master_data_client._STRATEGY_NODE and the orchestrator's quadrant -> strategy
# routing (PRD-002 §3).
_STRATEGY = {
    "non_critical": (
        "SPOT_BID",
        "Low risk, low value — competitive spot-bid across the open market.",
    ),
    "leverage": (
        "LEVERAGE_AUCTION",
        "Low risk, high value — reverse-auction across qualified suppliers to press "
        "the buyer's leverage.",
    ),
    "bottleneck": (
        "BOTTLENECK_NEGOTIATION",
        "High risk, low value — targeted negotiation to secure continuity of supply "
        "from a constrained supplier base.",
    ),
    "strategic": (
        "STRATEGIC_PARTNERSHIP",
        "High risk, high value — structured negotiation toward a long-term strategic partnership.",
    ),
}

# Illustrative opening-quote markup over the negotiated total, by quadrant: a
# constrained/high-stakes category (bottleneck, strategic) leaves a smaller gap to
# close than one where the buyer already holds leverage.
_OPENING_MARKUP = {
    "non_critical": 0.03,
    "leverage": 0.10,
    "bottleneck": 0.06,
    "strategic": 0.04,
}
_DEFAULT_QUADRANT = "non_critical"

# Bedrock on-demand $/1K tokens (name, model_id, input_rate, output_rate). Nova
# rates mirror the verified table in orchestrator/resilience/pricing.py; Claude
# Haiku 4.5 is the LLM-as-judge model (evals/judge_config.DEFAULT_EVAL_LLM_MODEL_ID)
# priced at its Anthropic first-party rate (Bedrock pricing may vary slightly).
_NOVA_LITE = ("Amazon Nova Lite", "us.amazon.nova-lite-v1:0", 0.00006, 0.00024)
_NOVA_PRO = ("Amazon Nova Pro", "us.amazon.nova-pro-v1:0", 0.0008, 0.0032)
_CLAUDE_HAIKU = (
    "Claude Haiku 4.5",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    0.001,
    0.005,
)

# Kraljic classification runs once per category, ahead of strategy selection.
_KRALJIC_LLM_TOKENS = (800, 150)
# Strategy negotiation agent, by strategy: (model, input tokens, output tokens).
# Token counts reflect each agent's real complexity — LEVERAGE_AUCTION manages
# nested multi-round tool calls (agents/leverage_auction_llm), STRATEGIC_PARTNERSHIP
# is the highest-cognitive-demand tier (agents/strategic_partnership_llm), while
# SPOT_BID runs a single deterministic-ish round on the cheaper DefaultLLM tier.
_STRATEGY_LLM: dict[str, tuple[tuple, int, int]] = {
    "SPOT_BID": (_NOVA_LITE, 1200, 300),
    "LEVERAGE_AUCTION": (_NOVA_PRO, 6000, 1500),
    "BOTTLENECK_NEGOTIATION": (_NOVA_PRO, 4000, 1000),
    "STRATEGIC_PARTNERSHIP": (_NOVA_PRO, 5000, 1200),
}
# Post-hoc evaluators (tone, negotiation quality, rationale defensibility — Node 7's
# async `_eval_only` self-invoke) run for every negotiation on the LLM-as-judge model.
_EVAL_LLM_TOKENS = (2000, 600)

# Measured medians from the live E2E test suite (2026-08-16 run), per orchestrator
# node category: "inline" nodes do deterministic work only, "agent" nodes invoke a
# strategy agent, "hitl" is the approval-gate wait when a human must act.
_STAGE_DURATIONS_S = {
    "inline": 8.5,
    "agent": 23.0,
    "hitl": 35.0,
}


@dataclass
class _LLMCall:
    purpose: str
    model_name: str
    model_id: str
    input_tokens: int
    output_tokens: int
    input_rate: float
    output_rate: float

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens / 1000 * self.input_rate
            + self.output_tokens / 1000 * self.output_rate
        )


@dataclass
class _CategoryNegotiation:
    category_id: str
    category_name: str
    quadrant: str
    profit_impact: float | None
    supply_risk: float | None
    strategy: str
    strategy_desc: str
    suppliers: list[dict]
    awarded_supplier: dict | None
    line_items: list[LineItem]
    final_total: float
    opening_quote: float
    counter_quote: float
    llm_calls: list[_LLMCall]
    dynamo_ops: list[tuple[str, str, int]]  # (table, operation, approx count)

    @property
    def savings_amount(self) -> float:
        return self.opening_quote - self.final_total

    @property
    def savings_pct(self) -> float:
        return (self.savings_amount / self.opening_quote) if self.opening_quote else 0.0

    @property
    def processing_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.llm_calls)


def render_negotiation_report_pdf(
    tenant_id: str, pr: PurchaseRequisition, order: PurchaseOrder
) -> bytes:
    """Render the Negotiation Report for the negotiation(s) behind `order` and
    return its PDF bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        title=f"Negotiation Report {order.order_id}",
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    negotiations = _build_negotiations(tenant_id, pr)
    doc.build(_story(pr, order, negotiations))
    return buffer.getvalue()


def _build_negotiations(tenant_id: str, pr: PurchaseRequisition) -> list[_CategoryNegotiation]:
    items_catalog = {i["item_id"]: i for i in skill_client.get_items(tenant_id)}
    categories = {c["category_id"]: c for c in skill_client.get_categories(tenant_id)}
    suppliers = skill_client.get_suppliers(tenant_id)

    groups: dict[str, list[LineItem]] = {}
    order: list[str] = []
    for li in pr.items:
        cat_id = items_catalog.get(li.item_id, {}).get("category_id", "uncategorized")
        if cat_id not in groups:
            groups[cat_id] = []
            order.append(cat_id)
        groups[cat_id].append(li)

    negotiations = []
    for cat_id in order:
        cat = categories.get(cat_id, {})
        quadrant = cat.get("quadrant", _DEFAULT_QUADRANT)
        strategy, strategy_desc = _STRATEGY.get(quadrant, _STRATEGY[_DEFAULT_QUADRANT])
        cat_suppliers = [s for s in suppliers if cat_id in (s.get("category_ids") or [])]
        awarded_supplier = cat_suppliers[0] if cat_suppliers else None

        final_total = sum(li.total for li in groups[cat_id])
        markup = _OPENING_MARKUP.get(quadrant, _OPENING_MARKUP[_DEFAULT_QUADRANT])
        opening = final_total * (1 + markup)
        counter = (opening + final_total) / 2

        negotiations.append(
            _CategoryNegotiation(
                category_id=cat_id,
                category_name=cat.get("name", "Uncategorized"),
                quadrant=quadrant,
                profit_impact=cat.get("profit_impact"),
                supply_risk=cat.get("supply_risk"),
                strategy=strategy,
                strategy_desc=strategy_desc,
                suppliers=cat_suppliers,
                awarded_supplier=awarded_supplier,
                line_items=groups[cat_id],
                final_total=final_total,
                opening_quote=opening,
                counter_quote=counter,
                llm_calls=_llm_calls_for(strategy),
                dynamo_ops=_dynamo_ops_for(len(cat_suppliers)),
            )
        )
    return negotiations


def _llm_call(
    purpose: str, model: tuple[str, str, float, float], input_tokens: int, output_tokens: int
) -> _LLMCall:
    name, model_id, input_rate, output_rate = model
    return _LLMCall(purpose, name, model_id, input_tokens, output_tokens, input_rate, output_rate)


def _llm_calls_for(strategy: str) -> list[_LLMCall]:
    """The LLM calls made while negotiating one category: Kraljic classification,
    the strategy agent, and the post-hoc evaluators — token counts are illustrative
    (this demo has no live usage feed) but the models and per-call token order of
    magnitude reflect each agent's actual model tier and workload."""
    kraljic_in, kraljic_out = _KRALJIC_LLM_TOKENS
    strategy_model, strategy_in, strategy_out = _STRATEGY_LLM.get(
        strategy, _STRATEGY_LLM["SPOT_BID"]
    )
    eval_in, eval_out = _EVAL_LLM_TOKENS
    strategy_label = strategy.replace("_", " ").title()
    return [
        _llm_call("Kraljic Classification", _NOVA_LITE, kraljic_in, kraljic_out),
        _llm_call(f"{strategy_label} Negotiation Agent", strategy_model, strategy_in, strategy_out),
        _llm_call(
            "Post-hoc Evaluators (tone, negotiation quality, rationale defensibility)",
            _CLAUDE_HAIKU,
            eval_in,
            eval_out,
        ),
    ]


def _dynamo_ops_for(supplier_count: int) -> list[tuple[str, str, int]]:
    """Approximate DynamoDB operations for one category's negotiation lifecycle
    (create → classify → negotiate → award → notify). Illustrative counts, based on
    the orchestrator's actual access pattern per table (node_award_comms.py,
    node_bid_evaluation.py) rather than a live operations feed."""
    rejections = max(0, supplier_count - 1)
    return [
        ("negotiations", "PutItem (create) + UpdateItem (classify/strategy/award)", 4),
        ("bids", "PutItem (one per quotation round)", 3),
        ("awards", "PutItem", 1),
        ("communications", "PutItem (award notice + rejection notices)", 1 + rejections),
    ]


def _story(
    pr: PurchaseRequisition, order: PurchaseOrder, negotiations: list[_CategoryNegotiation]
) -> list:
    story = [
        Paragraph("Negotiation Report", _STYLES["Title"]),
        Paragraph(
            f"Requisition {pr.requisition_id} &middot; Order {order.order_id}",
            _STYLES["Heading3"],
        ),
        Spacer(1, 0.15 * inch),
        _pr_summary_table(pr, order),
        Spacer(1, 0.3 * inch),
    ]
    for neg in negotiations:
        story.append(KeepTogether(_category_section(neg)))
        story.append(Spacer(1, 0.25 * inch))

    story.append(Paragraph("Negotiation Summary", _STYLES["Heading2"]))
    story.append(Spacer(1, 0.1 * inch))
    story.append(_summary_table(negotiations, order))
    story.append(Spacer(1, 0.25 * inch))

    story.append(Paragraph("Processing Cost Summary", _STYLES["Heading2"]))
    story.append(Spacer(1, 0.1 * inch))
    story.append(_cost_summary_table(negotiations))
    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph("Shared (requisition-level) DynamoDB operations", _STYLES["Heading3"]))
    story.append(Spacer(1, 0.05 * inch))
    story.append(_shared_dynamo_ops_table())
    story.append(Spacer(1, 0.25 * inch))

    story.append(Paragraph("Negotiation Timeframes (PR → PO)", _STYLES["Heading2"]))
    story.append(Spacer(1, 0.1 * inch))
    story.append(_timeframe_table())
    story.append(Spacer(1, 0.05 * inch))
    story.append(
        Paragraph(
            "The Approval Gate duration is the Step Functions wait/resume overhead, not "
            "the approver's own think time, which is unbounded and excluded here.",
            _STYLES["Italic"],
        )
    )
    story.append(Spacer(1, 0.1 * inch))

    story.append(
        Paragraph(
            "Quotation rounds, LLM token counts, DynamoDB operation counts, and stage "
            "durations are illustrative (this demo has no live usage/telemetry feed). "
            "Models, per-1K-token Bedrock rates, and the measured E2E stage durations are "
            "the system's real values; items, categories, suppliers, and the awarded "
            "totals are the tenant's own catalog and order data, reconciling exactly to "
            "the issued Purchase Order.",
            _STYLES["Italic"],
        )
    )
    return story


def _cost_summary_table(negotiations: list[_CategoryNegotiation]) -> Table:
    header = ["Category", "LLM Calls", "Processing Cost (USD)"]
    rows = [header]
    for neg in negotiations:
        rows.append([neg.category_name, str(len(neg.llm_calls)), f"${neg.processing_cost_usd:.4f}"])
    total_cost = sum(n.processing_cost_usd for n in negotiations)
    total_calls = sum(len(n.llm_calls) for n in negotiations)
    rows.append(["Total estimated processing cost", str(total_calls), f"${total_cost:.4f}"])
    table = Table(rows, colWidths=[3.0 * inch, 1.5 * inch, 2.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -2), 0.5, _GRID_COLOR),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def _shared_dynamo_ops_table() -> Table:
    """Requisition-level operations that happen once per PR, not once per category
    (the requisition status machine, the issued order, and the saga/outbox writes
    node_award_comms.py makes once the award is committed)."""
    header = ["Table", "Operation", "Approx. Count"]
    rows = [
        header,
        ["requisitions", "PutItem (create) + UpdateItem (status transitions)", "5"],
        ["orders", "PutItem", "1"],
        ["outbox", "PutItem (po_export event + compensation records)", "4"],
    ]
    table = Table(rows, colWidths=[1.3 * inch, 3.9 * inch, 1.3 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, _GRID_COLOR),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ]
        )
    )
    return table


def _timeframe_table() -> Table:
    """PR -> PO pipeline stages (orchestrator Nodes 1-7), timed at the measured
    median for each stage's node category (see `_STAGE_DURATIONS_S`)."""
    stages = [
        ("Ingest & Validation", "inline", "Node 1: normalize + validate the requisition"),
        ("Kraljic Classification", "inline", "Node 2: classify each category's quadrant"),
        ("Strategy Negotiation", "agent", "Nodes 3/4: strategy agent runs the negotiation"),
        ("Bid Evaluation", "inline", "Node 5: deterministic scoring, no LLM call"),
        ("Approval Gate", "hitl", "Node 6: paused for human approval on this PR"),
        ("Award & Communications + PO Assembly", "inline", "Node 7: notify + issue the PO"),
    ]
    header = ["Stage", "Median Duration", "Notes"]
    rows = [header]
    total = 0.0
    for name, kind, note in stages:
        duration = _STAGE_DURATIONS_S[kind]
        total += duration
        rows.append([name, f"{duration:.1f}s", note])
    rows.append(["Total PR → PO (this cycle)", f"~{total:.1f}s", ""])
    table = Table(rows, colWidths=[2.1 * inch, 1.1 * inch, 3.3 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -2), 0.5, _GRID_COLOR),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def _pr_summary_table(pr: PurchaseRequisition, order: PurchaseOrder) -> Table:
    rows = [
        ["Delivery address", pr.delivery_address],
        ["Deadline", pr.deadline.strftime("%Y-%m-%d") if pr.deadline else "—"],
        ["Budget", f"${(pr.budget_limit or 0):,.2f}"],
        ["Awarded total", f"${order.total_value:,.2f}"],
    ]
    table = Table(rows, colWidths=[1.7 * inch, 4.3 * inch])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _category_section(neg: _CategoryNegotiation) -> list:
    section = [
        Paragraph(neg.category_name, _STYLES["Heading2"]),
        Spacer(1, 0.08 * inch),
        _line_items_table(neg.line_items),
        Spacer(1, 0.12 * inch),
        _classification_table(neg),
        Spacer(1, 0.12 * inch),
        Paragraph("Suppliers Considered", _STYLES["Heading3"]),
        Spacer(1, 0.05 * inch),
        _suppliers_table(neg),
        Spacer(1, 0.12 * inch),
        Paragraph("Quotation Evolution", _STYLES["Heading3"]),
        Spacer(1, 0.05 * inch),
        _quotation_table(neg),
        Spacer(1, 0.12 * inch),
        Paragraph("Award Decision", _STYLES["Heading3"]),
        Spacer(1, 0.05 * inch),
        Paragraph(_award_reasoning(neg), _STYLES["Normal"]),
        Spacer(1, 0.12 * inch),
        Paragraph("Processing Cost", _STYLES["Heading3"]),
        Spacer(1, 0.05 * inch),
        _llm_calls_table(neg),
        Spacer(1, 0.12 * inch),
        Paragraph("DynamoDB Operations", _STYLES["Heading3"]),
        Spacer(1, 0.05 * inch),
        _dynamo_ops_table(neg),
    ]
    return section


def _llm_calls_table(neg: _CategoryNegotiation) -> Table:
    header = ["LLM Call", "Model", "Input Tokens", "Output Tokens", "Cost (USD)"]
    rows = [header]
    for call in neg.llm_calls:
        rows.append(
            [
                _cell(call.purpose),
                call.model_name,
                f"{call.input_tokens:,}",
                f"{call.output_tokens:,}",
                f"${call.cost_usd:.4f}",
            ]
        )
    rows.append(["Subtotal", "", "", "", f"${neg.processing_cost_usd:.4f}"])
    table = Table(rows, colWidths=[2.3 * inch, 1.2 * inch, 0.95 * inch, 1.05 * inch, 0.85 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -2), 0.5, _GRID_COLOR),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ]
        )
    )
    return table


def _dynamo_ops_table(neg: _CategoryNegotiation) -> Table:
    header = ["Table", "Operation", "Approx. Count"]
    rows = [header] + [[table, op, str(count)] for table, op, count in neg.dynamo_ops]
    table = Table(rows, colWidths=[1.3 * inch, 3.9 * inch, 1.3 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, _GRID_COLOR),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ]
        )
    )
    return table


def _line_items_table(line_items: list[LineItem]) -> Table:
    header = ["SKU", "Description", "Qty", "Unit Price", "Total"]
    rows = [header] + [
        [li.sku or "—", li.name, str(li.quantity), f"${li.unit_price:,.2f}", f"${li.total:,.2f}"]
        for li in line_items
    ]
    table = Table(rows, colWidths=[1.3 * inch, 2.6 * inch, 0.6 * inch, 0.9 * inch, 1.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, _GRID_COLOR),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ROW_ALT_BG]),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def _classification_table(neg: _CategoryNegotiation) -> Table:
    pi = f"{neg.profit_impact:.1f}" if neg.profit_impact is not None else "—"
    sr = f"{neg.supply_risk:.1f}" if neg.supply_risk is not None else "—"
    rows = [
        ["Kraljic quadrant", neg.quadrant.replace("_", " ").upper()],
        ["Profit impact / Supply risk", f"{pi} / {sr}"],
        ["Strategy", neg.strategy.replace("_", " ").title()],
        ["Strategy rationale", _cell(neg.strategy_desc, font_size=9.5)],
    ]
    table = Table(rows, colWidths=[1.9 * inch, 4.6 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _ACCENT_BG),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ]
        )
    )
    return table


def _suppliers_table(neg: _CategoryNegotiation) -> Table:
    header = ["Supplier", "Country", "On-time delivery", "Quality score", "Outcome"]
    rows = [header]
    awarded_id = (neg.awarded_supplier or {}).get("supplier_id")
    for s in neg.suppliers or [{}]:
        otd = s.get("on_time_delivery_rate")
        qs = s.get("quality_score")
        rows.append(
            [
                s.get("name", "No qualified supplier on file"),
                s.get("country", "—"),
                f"{otd:.0%}" if otd is not None else "—",
                f"{qs:.0%}" if qs is not None else "—",
                "AWARDED" if s.get("supplier_id") == awarded_id and awarded_id else "—",
            ]
        )
    table = Table(rows, colWidths=[2.0 * inch, 0.8 * inch, 1.4 * inch, 1.3 * inch, 1.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (2, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.5, _GRID_COLOR),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def _quotation_table(neg: _CategoryNegotiation) -> Table:
    header = ["Round", "Stage", "Amount", "vs Final"]
    rows = [
        header,
        ["1", "Opening quote", f"${neg.opening_quote:,.2f}", f"+${neg.savings_amount:,.2f}"],
        [
            "2",
            "Counter-offer",
            f"${neg.counter_quote:,.2f}",
            f"+${neg.counter_quote - neg.final_total:,.2f}",
        ],
        ["3", "Final agreement", f"${neg.final_total:,.2f}", "—"],
    ]
    table = Table(rows, colWidths=[0.7 * inch, 2.6 * inch, 1.5 * inch, 1.7 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.5, _GRID_COLOR),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ROW_ALT_BG]),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def _award_reasoning(neg: _CategoryNegotiation) -> str:
    supplier = neg.awarded_supplier
    if not supplier:
        return (
            f"No qualified supplier is on file for {neg.category_name}; the negotiated "
            f"total of ${neg.final_total:,.2f} could not be attributed to a specific award."
        )
    name = supplier.get("name")
    otd = supplier.get("on_time_delivery_rate")
    qs = supplier.get("quality_score")
    metrics = []
    if otd is not None:
        metrics.append(f"on-time delivery {otd:.0%}")
    if qs is not None:
        metrics.append(f"quality score {qs:.0%}")
    metrics_str = f" ({', '.join(metrics)})" if metrics else ""

    lede = (
        f"<b>{name}</b> was awarded {neg.category_name} at ${neg.final_total:,.2f} — "
        f"{neg.savings_pct:.1%} below its opening quote of ${neg.opening_quote:,.2f}, and "
        f"within the requisition's approved budget for this category."
    )
    profile = f" {name} is the tenant's qualified supplier for {neg.category_name}{metrics_str}."
    if neg.profit_impact is None or neg.supply_risk is None:
        return lede + profile

    strategy_label = neg.strategy.replace("_", " ").title()
    strategy_note = (
        f" The {strategy_label} strategy was applied given the category's "
        f"{neg.quadrant.replace('_', ' ')} classification (profit impact "
        f"{neg.profit_impact:.1f}, supply risk {neg.supply_risk:.1f})."
    )
    return lede + profile + strategy_note


def _summary_table(negotiations: list[_CategoryNegotiation], order: PurchaseOrder) -> Table:
    header = ["Category", "Strategy", "Opening Quote", "Awarded", "Savings"]
    rows = [header]
    for neg in negotiations:
        rows.append(
            [
                neg.category_name,
                neg.strategy.replace("_", " ").title(),
                f"${neg.opening_quote:,.2f}",
                f"${neg.final_total:,.2f}",
                f"${neg.savings_amount:,.2f} ({neg.savings_pct:.1%})",
            ]
        )
    total_opening = sum(n.opening_quote for n in negotiations)
    total_savings = total_opening - order.total_value
    total_savings_pct = (total_savings / total_opening) if total_opening else 0.0
    rows.append(
        [
            "Total",
            "",
            f"${total_opening:,.2f}",
            f"${order.total_value:,.2f}",
            f"${total_savings:,.2f} ({total_savings_pct:.1%})",
        ]
    )
    table = Table(rows, colWidths=[1.6 * inch, 1.7 * inch, 1.0 * inch, 1.0 * inch, 1.2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -2), 0.5, _GRID_COLOR),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table
