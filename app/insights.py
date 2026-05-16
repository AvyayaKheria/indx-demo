"""
CFO Intelligence Engine
-----------------------
Generates 6 AI-powered insights from dashboard KPIs.
Each insight is calibrated to the ACTUAL value ranges (permutation-based)
so a debt ratio of 0.62 gets a fundamentally different insight and action
than a debt ratio of 0.91.

Insights are cached to insights.json inside the session directory so
the Claude call only fires once per session.
"""

import os
import json
from pathlib import Path

MODEL = "claude-3-5-haiku-20241022"    # fast: ~1-2 s latency
MODEL_FALLBACK = "claude-3-haiku-20240307"  # fallback if 3.5 haiku not available


# ── Industry benchmarks injected into the prompt ─────────────────────────────

_BENCHMARKS = """
F&B INDUSTRY BENCHMARKS (use these to contextualise every insight):
- Gross margin:    55–70 % is healthy; below 50 % is a red flag
- EBITDA margin:   8–15 % is target; 0–8 % needs attention; negative = crisis
- Net margin:      3–9 % typical; negative = unsustainable without intervention
- Current ratio:   1.2–2.0 ideal; below 1.0 = cannot cover short-term debts;
                   above 3.0 = excess idle cash (opportunity cost)
- Debt / equity:   below 0.5 = very conservative (under-leveraged growth risk);
                   0.5–0.75 = conservative but healthy;
                   0.75–1.0 = moderate, monitor carefully;
                   above 1.0 = elevated; above 1.5 = high leverage, risk of distress
- Revenue growth:  healthy F&B scales 10–25 % YoY
"""


def _kpi_block(kpis: dict) -> str:
    rev   = kpis.get("total_revenue", 0)
    gp    = kpis.get("gross_profit",  0)
    gm    = kpis.get("gross_margin",  0)
    eb    = kpis.get("ebitda",        0)
    em    = kpis.get("ebitda_margin", 0)
    np_   = kpis.get("net_profit",    0)
    nm    = kpis.get("net_margin",    0)
    ta    = kpis.get("total_assets",  0)
    te    = kpis.get("total_equity",  0)
    tl    = kpis.get("total_liabilities", 0)
    cr    = kpis.get("current_ratio",  0)
    de    = kpis.get("debt_to_equity", 0)
    return (
        f"Total Revenue:         ${rev:,.0f}\n"
        f"Gross Profit:          ${gp:,.0f}  ({gm:.1f}% margin)\n"
        f"EBITDA:                ${eb:,.0f}  ({em:.1f}% margin)\n"
        f"Net Profit (NPAT):     ${np_:,.0f}  ({nm:.1f}% margin)\n"
        f"Total Assets:          ${ta:,.0f}\n"
        f"Total Equity:          ${te:,.0f}\n"
        f"Total Liabilities:     ${tl:,.0f}\n"
        f"Current Ratio:         {cr:.2f}x\n"
        f"Debt / Equity:         {de:.2f}x\n"
    )


_PROMPT_TEMPLATE = """\
You are a senior CFO advisor specialising in food & beverage businesses.
A founder has just uploaded their financials. Analyse the metrics below and \
generate exactly 6 sharp, actionable insights — the kind a great CFO would \
tell the founder in a 15-minute board meeting.

FINANCIAL METRICS (full year):
{kpi_block}
{benchmarks}

RULES:
1. Each insight must reference the EXACT numbers (e.g. "Your EBITDA of -$10,000
   means…") — never speak in generalities.
2. Severity is determined by how far the metric deviates from the benchmark:
   - critical  → dangerous territory, needs immediate action this month
   - warning   → needs attention within the quarter
   - positive  → genuine competitive strength worth protecting/scaling
   - neutral   → acceptable, minor optimisation possible
3. The "action" must be ONE concrete next step (e.g. "Negotiate supplier
   contracts to cut COGS by 5 percentage points — that adds $66K gross profit").
4. Cover these six categories (one insight each):
   revenue | profitability | cost_structure | liquidity | leverage | risk
5. Return ONLY a valid JSON array — no markdown, no explanation.

JSON schema (return exactly this structure):
[
  {{
    "category":  "revenue",
    "title":     "8-word max headline",
    "severity":  "critical|warning|positive|neutral",
    "body":      "2–3 sentences. Finding + why it matters + benchmark comparison.",
    "action":    "One specific, quantified next step."
  }}
]
"""


def generate_insights(kpis: dict) -> tuple[list[dict], str | None]:
    """
    Call Claude and return (insights, error_message).
    insights is [] and error_message is set when something goes wrong.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return [], "no_key"

    try:
        import anthropic
    except ImportError:
        return [], "anthropic package not installed"

    prompt = _PROMPT_TEMPLATE.format(
        kpi_block=_kpi_block(kpis),
        benchmarks=_BENCHMARKS,
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        # Try primary model, fall back to haiku-3 if primary isn't accessible
        for model_id in (MODEL, MODEL_FALLBACK):
            try:
                resp = client.messages.create(
                    model=model_id,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                break
            except Exception as model_err:
                if model_id == MODEL_FALLBACK:
                    raise model_err
                continue
        raw = resp.content[0].text.strip()

        # Strip any accidental markdown fences
        for fence in ("```json", "```"):
            if fence in raw:
                raw = raw.split(fence, 1)[-1].rsplit("```", 1)[0].strip()
                break

        start = raw.find("[")
        if start != -1:
            raw = raw[start:]

        insights = json.loads(raw)
        clean = []
        for item in insights:
            clean.append({
                "category": item.get("category", "general"),
                "title":    item.get("title",    "Insight"),
                "severity": item.get("severity", "neutral"),
                "body":     item.get("body",     ""),
                "action":   item.get("action",   ""),
            })
        return clean, None

    except Exception as exc:
        return [], str(exc)


def get_insights(session_dir, kpis: dict) -> tuple[list[dict], str | None]:
    """Return (insights, error). Uses cache when available."""
    cache_path = None if session_dir is None else Path(session_dir) / "insights.json"

    if cache_path and cache_path.exists():
        try:
            return json.loads(cache_path.read_text()), None
        except Exception:
            pass

    insights, err = generate_insights(kpis)

    if cache_path and insights:
        try:
            cache_path.write_text(json.dumps(insights, indent=2))
        except Exception:
            pass

    return insights, err
