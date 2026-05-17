"""
CFO Intelligence Engine
-----------------------
Two layers:
  1. Rule-based insights  — instant, zero API calls (generate_insights / get_insights)
  2. AI bullet insights   — one Claude call per session, cached (generate_ai_bullets / get_ai_bullets)
"""

import os
import re
from pathlib import Path
import json

# In-memory cache for demo (data_dir=None) so we don't re-call on every page load
_demo_ai_cache: list[str] | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(v: float) -> str:
    """Format a dollar value as $XK or $X.XM."""
    v = abs(v)
    if v >= 1_000_000:
        return f"${v / 1e6:.2f}M"
    return f"${v / 1e3:.0f}K"


# ── The six rule sets ─────────────────────────────────────────────────────────

def _insight_gross_margin(kpis: dict) -> dict:
    rev = kpis.get("total_revenue", 0)
    gm  = kpis.get("gross_margin",  0)
    gp  = kpis.get("gross_profit",  0)

    if gm >= 65:
        return dict(
            category="revenue",
            title="Gross margin exceeds industry average",
            severity="positive",
            body=(
                f"Your gross margin of {gm:.1f}% is above the F&B benchmark of 60–65%, "
                f"generating {_fmt(gp)} in gross profit on {_fmt(rev)} revenue. "
                "Strong pricing power and supplier discipline are driving this."
            ),
            action=(
                f"Lock in quarterly supplier contracts now. Each 1 percentage-point "
                f"improvement adds {_fmt(rev * 0.01)} to gross profit."
            ),
        )
    elif gm >= 50:
        gap  = 62.5 - gm
        lost = rev * gap / 100
        return dict(
            category="revenue",
            title="Gross margin below F&B benchmark",
            severity="warning",
            body=(
                f"Your gross margin of {gm:.1f}% trails the F&B industry average of 60–65%. "
                f"That {gap:.1f}pp gap is costing you roughly {_fmt(lost)} in foregone gross "
                "profit on your current revenue base."
            ),
            action=(
                "Audit your top 10 menu items by contribution margin. Renegotiate supplier "
                "terms or reprice low-margin items by 5–8% to close the gap within one quarter."
            ),
        )
    else:
        gap  = 60 - gm
        lost = rev * gap / 100
        return dict(
            category="revenue",
            title="Gross margin is critically low",
            severity="critical",
            body=(
                f"At {gm:.1f}%, gross margin is well below the F&B floor of 50%. "
                f"You are leaving {_fmt(lost)} on the table versus a benchmark operation "
                "running on the same revenue. COGS is consuming too much of every dollar earned."
            ),
            action=(
                "Escalate COGS review immediately. Target a 5pp margin recovery within "
                "90 days through supplier renegotiation, menu redesign, and portion control."
            ),
        )


def _insight_ebitda(kpis: dict) -> dict:
    rev  = kpis.get("total_revenue",   0)
    eb   = kpis.get("ebitda",          0)
    em   = kpis.get("ebitda_margin",   0)

    if em >= 10:
        return dict(
            category="profitability",
            title="EBITDA margin is healthy",
            severity="positive",
            body=(
                f"EBITDA of {_fmt(eb)} ({em:.1f}% margin) is within the F&B target range of "
                "8–15%. This means operating costs are well-controlled relative to revenue, "
                "leaving real cash available for debt service and reinvestment."
            ),
            action=(
                "Maintain OpEx discipline. Model what a 2pp EBITDA improvement would yield — "
                f"that's {_fmt(rev * 0.02)} in additional operating cash annually."
            ),
        )
    elif em >= 0:
        target_gap = 8 - em
        upside     = rev * target_gap / 100
        return dict(
            category="profitability",
            title="EBITDA margin is thin — needs attention",
            severity="warning",
            body=(
                f"EBITDA of {_fmt(eb)} ({em:.1f}% margin) is below the F&B target of 8–15%. "
                f"Closing the {target_gap:.1f}pp gap to the low end of benchmark would add "
                f"{_fmt(upside)} to operating earnings on your current revenue."
            ),
            action=(
                "Identify your top three operating cost lines and model a 10% reduction on "
                "each. Payroll efficiency and occupancy costs are typically the fastest levers."
            ),
        )
    else:
        shortfall = abs(eb)
        return dict(
            category="profitability",
            title="Negative EBITDA — burning operating cash",
            severity="critical",
            body=(
                f"EBITDA of {_fmt(eb)} ({em:.1f}% margin) means the business is losing "
                f"{_fmt(shortfall)} before interest, tax, and depreciation. This is not "
                "a profitability problem — it is an operational cash burn problem requiring "
                "immediate intervention."
            ),
            action=(
                f"You need to cut {_fmt(shortfall)} from OpEx or grow revenue to reach "
                "EBITDA breakeven. Freeze all discretionary spend and review headcount "
                "against revenue per employee this week."
            ),
        )


def _insight_net_profit(kpis: dict) -> dict:
    rev  = kpis.get("total_revenue", 0)
    np_  = kpis.get("net_profit",    0)
    nm   = kpis.get("net_margin",    0)

    if nm >= 5:
        return dict(
            category="profitability",
            title="Net margin is strong for F&B",
            severity="positive",
            body=(
                f"Net profit of {_fmt(np_)} ({nm:.1f}% margin) is above the F&B average of "
                "3–9%. After interest, tax, and depreciation the business is retaining real "
                "earnings — a sign the capital structure and tax position are managed well."
            ),
            action=(
                "Begin modelling how retained earnings can fund expansion — a second "
                "location or catering fleet typically requires 12–18 months of net profit."
            ),
        )
    elif nm >= 0:
        needed = rev * (3 - nm) / 100
        return dict(
            category="profitability",
            title="Net profit exists but margins are slim",
            severity="warning",
            body=(
                f"Net profit of {_fmt(np_)} ({nm:.1f}% margin) is below the F&B floor of 3%. "
                "The business is technically profitable, but a single bad month or unexpected "
                "cost spike could push it into loss."
            ),
            action=(
                f"To reach 3% net margin you need {_fmt(needed)} more profit. "
                "Focus on interest cost reduction (refinance if possible) and "
                "ensuring all tax credits and deductions are being claimed."
            ),
        )
    else:
        loss = abs(np_)
        return dict(
            category="profitability",
            title="Business is running at a net loss",
            severity="critical",
            body=(
                f"Net loss of {_fmt(loss)} ({nm:.1f}% margin) means equity is being eroded "
                "every period this continues. If losses persist, the path leads to either "
                "equity injection, refinancing, or insolvency."
            ),
            action=(
                f"Build a 13-week cash flow forecast immediately. Identify the month "
                f"cash runs out and work backwards — you need to close {_fmt(loss)} "
                "in annual losses through revenue growth, cost cuts, or both."
            ),
        )


def _insight_liquidity(kpis: dict) -> dict:
    cr   = kpis.get("current_ratio",        0)
    ca   = kpis.get("total_assets",         0)  # approximation
    cl   = kpis.get("total_liabilities",    0)

    if cr >= 2.5:
        return dict(
            category="liquidity",
            title="Strong liquidity — but cash may be idle",
            severity="neutral",
            body=(
                f"Your current ratio of {cr:.2f}x means you hold ${cr:.1f} in short-term "
                "assets for every $1 of short-term obligations — well above the F&B ideal "
                "of 1.2–2.0x. While this signals safety, excess liquidity may indicate "
                "underdeployed cash that could be working harder."
            ),
            action=(
                "Review whether excess cash should be deployed into inventory efficiencies, "
                "early supplier payment discounts, or short-term interest-bearing instruments."
            ),
        )
    elif cr >= 1.2:
        return dict(
            category="liquidity",
            title="Liquidity is in the healthy range",
            severity="positive",
            body=(
                f"A current ratio of {cr:.2f}x sits within the F&B ideal range of 1.2–2.0x. "
                "The business can comfortably cover its short-term obligations without "
                "straining operations or requiring emergency financing."
            ),
            action=(
                "Maintain this buffer. Stress-test liquidity against a 20% revenue decline "
                "scenario to confirm the ratio stays above 1.0x under pressure."
            ),
        )
    elif cr >= 1.0:
        return dict(
            category="liquidity",
            title="Liquidity is tight — monitor weekly",
            severity="warning",
            body=(
                f"Your current ratio of {cr:.2f}x means short-term assets only just cover "
                "short-term liabilities. The F&B ideal is 1.2–2.0x. Any unexpected cost "
                "or revenue shortfall could create a cash crunch."
            ),
            action=(
                "Set up a weekly cash flow dashboard. Negotiate extended payment terms "
                "with your two largest suppliers to widen the liquidity buffer immediately."
            ),
        )
    else:
        return dict(
            category="liquidity",
            title="Liquidity crisis — liabilities exceed assets",
            severity="critical",
            body=(
                f"A current ratio of {cr:.2f}x means current liabilities exceed current "
                "assets. The business cannot cover its short-term obligations from existing "
                "liquid resources — a genuine solvency risk if unaddressed."
            ),
            action=(
                "Contact your lender this week to discuss an emergency working capital "
                "facility. Simultaneously, accelerate receivables collection and defer "
                "all non-critical payables to extend your runway."
            ),
        )


def _insight_leverage(kpis: dict) -> dict:
    de  = kpis.get("debt_to_equity",        0)
    tl  = kpis.get("total_liabilities",     0)
    te  = kpis.get("total_equity",          0)

    if de > 1.5:
        return dict(
            category="leverage",
            title="Leverage is dangerously high",
            severity="critical",
            body=(
                f"Debt-to-equity of {de:.2f}x means creditors own ${de:.1f} for every $1 "
                f"of equity — well above the F&B danger threshold of 1.5x. "
                f"With {_fmt(tl)} in total liabilities against {_fmt(te)} equity, "
                "the balance sheet is fragile."
            ),
            action=(
                "Prioritise debt reduction over growth. Target paying down high-interest "
                "facilities first. Do not take on new debt until D/E falls below 1.0x."
            ),
        )
    elif de > 1.0:
        return dict(
            category="leverage",
            title="Leverage elevated — watch debt service",
            severity="warning",
            body=(
                f"Debt-to-equity of {de:.2f}x is above the preferred F&B ceiling of 1.0x. "
                f"You carry {_fmt(tl)} in liabilities against {_fmt(te)} in equity. "
                "This is manageable today but leaves little room for revenue downside."
            ),
            action=(
                "Model your debt service coverage ratio. If EBITDA / annual debt service "
                "is below 1.25x, begin refinancing conversations with your bank now."
            ),
        )
    elif de >= 0.5:
        return dict(
            category="leverage",
            title="Leverage is conservative and healthy",
            severity="positive",
            body=(
                f"Debt-to-equity of {de:.2f}x sits in the F&B sweet spot of 0.5–1.0x. "
                f"You have {_fmt(te)} in equity backing {_fmt(tl)} in liabilities — "
                "a balance that supports growth without excessive financial risk."
            ),
            action=(
                "You have headroom to leverage for strategic growth. Model whether a "
                "targeted debt facility at current rates would generate a positive ROI "
                "on a second revenue stream or expansion."
            ),
        )
    else:
        return dict(
            category="leverage",
            title="Under-leveraged — growth capital available",
            severity="neutral",
            body=(
                f"Debt-to-equity of {de:.2f}x is very conservative — below 0.5x means "
                "the business is almost entirely equity-funded. While this is low-risk, "
                "it may indicate an opportunity to use cheap debt to accelerate growth "
                "rather than relying solely on retained earnings."
            ),
            action=(
                "Speak to your bank about a growth facility. At conservative leverage "
                "you would qualify for competitive rates — model the ROI of deploying "
                "debt into your highest-returning revenue channel."
            ),
        )


def _insight_risk(kpis: dict) -> dict:
    """Composite risk insight based on the combination of metrics."""
    em  = kpis.get("ebitda_margin",  0)
    nm  = kpis.get("net_margin",     0)
    cr  = kpis.get("current_ratio",  0)
    de  = kpis.get("debt_to_equity", 0)
    gm  = kpis.get("gross_margin",   0)
    rev = kpis.get("total_revenue",  0)

    # Score risk factors
    risk_flags = sum([
        em  < 0,
        nm  < 0,
        cr  < 1.2,
        de  > 1.0,
        gm  < 50,
    ])

    if risk_flags >= 3:
        return dict(
            category="risk",
            title="Multiple red flags — act now",
            severity="critical",
            body=(
                f"Your financials show {risk_flags} out of 5 key risk indicators in the "
                "danger zone simultaneously. When negative EBITDA, thin liquidity, and "
                "elevated leverage occur together, the compounding effect is far more "
                "dangerous than any single metric in isolation."
            ),
            action=(
                "Convene an emergency financial review with your accountant this week. "
                "Prioritise cash preservation above all else — cut costs before chasing revenue."
            ),
        )
    elif risk_flags == 2:
        return dict(
            category="risk",
            title="Two risk areas need attention this quarter",
            severity="warning",
            body=(
                f"Two of your five core financial health indicators are outside the safe "
                "range. This is a yellow light — the business is not in crisis, but "
                "the risk profile is elevated enough that deterioration in one more area "
                "would create compounding pressure."
            ),
            action=(
                "Address the two weakest metrics within this quarter. Set a monthly "
                "KPI review cadence so problems are caught early rather than at year-end."
            ),
        )
    elif risk_flags == 1:
        return dict(
            category="risk",
            title="One risk area to address — otherwise healthy",
            severity="neutral",
            body=(
                "Four of your five core financial health indicators are within or above "
                "benchmark ranges. The business has a solid foundation — one area needs "
                "attention but the overall risk profile is manageable."
            ),
            action=(
                "Focus improvement effort on the one underperforming metric. "
                "With the rest of the business healthy, a targeted fix is achievable "
                "without distraction from growth initiatives."
            ),
        )
    else:
        return dict(
            category="risk",
            title="Financial health is strong across the board",
            severity="positive",
            body=(
                "All five core financial health indicators — gross margin, EBITDA, "
                "net profit, liquidity, and leverage — are within or above F&B benchmarks. "
                "This is a well-run business with a sound financial structure."
            ),
            action=(
                "With fundamentals solid, focus on growth. Model the unit economics "
                f"of scaling revenue by 20% — at your current margins that adds "
                f"{_fmt(rev * 0.20 * kpis.get('net_margin', 0) / 100)} in net profit."
            ),
        )


# ── Public API ────────────────────────────────────────────────────────────────

def generate_insights(kpis: dict) -> list[dict]:
    """Return 6 rule-based CFO insights from KPI values. Zero API calls."""
    return [
        _insight_gross_margin(kpis),
        _insight_ebitda(kpis),
        _insight_net_profit(kpis),
        _insight_liquidity(kpis),
        _insight_leverage(kpis),
        _insight_risk(kpis),
    ]


def get_insights(session_dir, kpis: dict) -> tuple[list[dict], str | None]:
    """Return (insights, error). Caches to insights.json per session."""
    cache_path = None if session_dir is None else Path(session_dir) / "insights.json"

    if cache_path and cache_path.exists():
        try:
            return json.loads(cache_path.read_text()), None
        except Exception:
            pass

    insights = generate_insights(kpis)

    if cache_path and insights:
        try:
            cache_path.write_text(json.dumps(insights, indent=2))
        except Exception:
            pass

    return insights, None


# ── AI bullet insights (one Claude call, cached) ──────────────────────────────

_AI_PROMPT = """\
You are a CFO advisor analysing a company's financial dashboard. Based on these figures, \
provide exactly 4 bullet points of sharp, specific financial insights. Be direct and \
actionable. Flag any concerns. Highlight any strengths. Reference specific numbers.

Financial data:

Total Revenue: ${total_revenue:,.0f}
Gross Profit: ${gross_profit:,.0f} ({gross_margin:.1f}% margin)
EBITDA: ${ebitda:,.0f} ({ebitda_margin:.1f}% margin)
Net Profit: ${net_profit:,.0f} ({net_margin:.1f}% margin)
Total Assets: ${total_assets:,.0f}
Total Equity: ${total_equity:,.0f}
Current Ratio: {current_ratio:.2f}x
Debt to Equity: {debt_to_equity:.2f}x

Return exactly 4 bullet points. Each bullet: one sentence, specific, references actual \
numbers. No preamble, no conclusion. Just the 4 bullets."""

def _best_model(client) -> tuple[str | None, str | None]:
    """
    Use models.list() to find the best model this account can actually access.
    Returns (model_id, error).
    Prefers sonnet > haiku > opus by name match.
    """
    try:
        available = [m.id for m in client.models.list().data]
    except Exception as e:
        return None, f"models.list() failed: {e}"

    if not available:
        return None, "No models returned by models.list()"

    # Preference order by substring match
    for pref in ("sonnet-4", "opus-4", "sonnet", "haiku", "claude"):
        for m in available:
            if pref in m.lower():
                return m, None

    return available[0], None   # fallback: whatever is first


def generate_ai_bullets(kpis: dict) -> tuple[list[str] | None, str | None]:
    """
    Call Claude and return (bullets, error).
    Uses models.list() to discover the correct model for this account.
    Never swallows exceptions silently.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None, "ANTHROPIC_API_KEY not set"

    try:
        import anthropic
    except ImportError:
        return None, "anthropic package not installed"

    prompt = _AI_PROMPT.format(**{k: kpis.get(k, 0) for k in [
        "total_revenue", "gross_profit", "gross_margin",
        "ebitda", "ebitda_margin", "net_profit", "net_margin",
        "total_assets", "total_equity", "current_ratio", "debt_to_equity",
    ]})

    client = anthropic.Anthropic(api_key=api_key)

    model_id, err = _best_model(client)
    if not model_id:
        return None, err

    try:
        resp = client.messages.create(
            model=model_id,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()

        bullets = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^[•\-\*·]\s*", "", line)
            line = re.sub(r"^\d+[\.\)]\s*", "", line)
            if line:
                bullets.append(line)

        if not bullets:
            return None, f"Claude ({model_id}) returned an empty response"

        return bullets[:4], None

    except Exception as exc:
        return None, f"Claude call failed ({model_id}): {exc}"


def get_ai_bullets(session_dir, kpis: dict) -> tuple[list[str] | None, str | None]:
    """Return cached AI bullets if available, else generate and cache."""
    global _demo_ai_cache

    # Demo — in-memory cache
    if session_dir is None:
        if _demo_ai_cache is not None:
            return _demo_ai_cache, None
        bullets, err = generate_ai_bullets(kpis)
        if bullets:
            _demo_ai_cache = bullets
        return bullets, err

    # Session — file cache
    cache_path = Path(session_dir) / "ai_insights.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text()), None
        except Exception:
            pass

    bullets, err = generate_ai_bullets(kpis)

    if bullets:
        try:
            cache_path.write_text(json.dumps(bullets))
        except Exception:
            pass

    return bullets, err
