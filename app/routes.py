import json
import os
import uuid
import shutil
import plotly
import plotly.graph_objects as go
import pandas as pd
from pathlib import Path
from flask import (Blueprint, render_template, request, redirect, url_for,
                   jsonify, send_from_directory, abort, send_file,
                   stream_with_context, Response)

from .loader import (
    load_balance_sheet, load_costs, load_pl, load_revenue, load_trial_balance,
    load_revenue_json, load_costs_json, load_pl_json,
    load_balance_sheet_json, load_trial_balance_json,
)
# extractor is kept for future use but not triggered in the upload flow

main = Blueprint("main", __name__)

# ── Upload storage ────────────────────────────────────────────────────────────
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# (form field key, saved filename, display label, expected min columns, col hint)
FILE_SLOTS = [
    ("revenue",       "revenue.xlsx",       "Revenue Sheet",    5,
     "Month, Dine-In, Delivery, Catering, Total"),
    ("costs",         "costs.xlsx",         "Cost Sheet",       6,
     "Month, COGS, Payroll, Rent, Marketing, Total"),
    ("pl",            "pl.xlsx",            "P&L Statement",    3,
     "Item, Amount, %"),
    ("balance_sheet", "balance_sheet.xlsx", "Balance Sheet",    2,
     "Item, Amount"),
    ("trial_balance", "trial_balance.xlsx", "Trial Balance",    3,
     "Account, Debit, Credit"),
]

# ── Design tokens ─────────────────────────────────────────────────────────────
NAVY   = "#0f2744"
TEAL   = "#0d9488"
TEAL2  = "#14b8a6"
TEAL3  = "#5eead4"
RED    = "#ef4444"
GREEN  = "#10b981"
SLATE  = "#64748b"
GRID   = "#f1f5f9"
BORDER = "#e2e8f0"

# Extended palette for dynamic channel/category colours
PALETTE = [NAVY, TEAL, TEAL2, TEAL3, "#2563eb", "#7c3aed", "#db2777", "#f59e0b"]

_FONT   = dict(family="Inter, system-ui, -apple-system, sans-serif", size=12, color="#475569")
_HOVER  = dict(
    bgcolor="white", bordercolor=BORDER,
    font=dict(family="Inter, system-ui, sans-serif", size=12, color="#1e293b"),
    namelength=-1,
)
_LEGEND = dict(
    orientation="h", y=-0.25, x=0,
    font=dict(size=11, color=SLATE),
    bgcolor="rgba(0,0,0,0)", borderwidth=0,
)
_XAXIS = dict(
    showgrid=False, linecolor=BORDER, linewidth=1,
    tickfont=dict(size=11, color=SLATE),
)
_YAXIS = dict(
    showgrid=True, gridcolor=GRID, gridwidth=1,
    linecolor=BORDER, linewidth=1,
    zeroline=True, zerolinecolor=BORDER, zerolinewidth=1,
    tickfont=dict(size=11, color=SLATE),
    tickprefix="$", tickformat=",.0f",
)


def _layout(title="", margin=None, y_title=None):
    yaxis = dict(_YAXIS)
    if y_title:
        yaxis["title"] = dict(text=y_title, font=dict(size=11, color=SLATE), standoff=12)
    m = margin or dict(l=80, r=24, t=48, b=64)
    return dict(
        paper_bgcolor="white", plot_bgcolor="white",
        font=_FONT, hoverlabel=_HOVER, legend=_LEGEND,
        xaxis=dict(_XAXIS), yaxis=yaxis, margin=m,
        title=dict(text=title, font=dict(size=13, color="#1e293b"),
                   x=0, xanchor="left", pad=dict(l=8, t=4)),
    )


def _fmt(v: float) -> str:
    v = abs(v)
    if v >= 1_000_000:
        return f"${v / 1e6:.2f}M"
    return f"${v / 1e3:.0f}K"


def _json(fig: go.Figure) -> str:
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def _donut(labels, values, colors, title, centre_top, centre_sub):
    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        hole=0.54,
        marker=dict(colors=colors, line=dict(color="white", width=2.5)),
        textinfo="none",
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f} · %{percent}<extra></extra>",
        sort=False, direction="clockwise",
    ))
    fig.add_annotation(
        text=f"<b>{centre_top}</b><br>{centre_sub}",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=13, color=NAVY), align="center",
    )
    fig.update_layout(
        paper_bgcolor="white", plot_bgcolor="white",
        font=_FONT, hoverlabel=_HOVER,
        title=dict(text=title, font=dict(size=13, color="#1e293b"),
                   x=0, xanchor="left", pad=dict(l=8, t=4)),
        showlegend=True,
        legend=dict(orientation="h", y=-0.08,
                    font=dict(size=11, color=SLATE), bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=20, r=20, t=48, b=72),
    )
    return fig


# ── File validation (fallback — used when no AI key) ─────────────────────────

def _validate_xlsx(path: Path, ncols_expected: int, label: str, col_hint: str):
    try:
        df = pd.read_excel(path, header=1, nrows=3)
    except Exception as e:
        return f"{label}: could not read file — {e}"
    if len(df.columns) < ncols_expected:
        return (
            f"{label} should have columns: {col_hint} "
            f"(found {len(df.columns)} column{'s' if len(df.columns) != 1 else ''})"
        )
    return None


# ── JSON vs raw-Excel detection ───────────────────────────────────────────────

def _has_extracted_json(data_dir) -> bool:
    """True when all AI-extracted JSON files exist in data_dir."""
    if not data_dir:
        return False
    p = Path(data_dir)
    return all(
        (p / f).exists() for f in [
            "revenue_extracted.json", "costs_extracted.json",
            "pl_extracted.json",      "balance_sheet_extracted.json",
        ]
    )


# ── In-memory result cache ────────────────────────────────────────────────────
# Key: str(data_dir) or "__demo__".  Value: (kpis, charts, cost_legend).
# Session data is immutable after upload so a simple dict is safe.
_DASH_CACHE: dict = {}


def _cache_key(data_dir) -> str:
    return "__demo__" if data_dir is None else str(data_dir)


# ── Core dashboard builder ────────────────────────────────────────────────────

def _build_dashboard_data(data_dir=None):
    """
    Load data and build all KPIs and charts.
    Uses AI-extracted JSON when available; falls back to raw Excel loaders.
    Returns (kpis dict, charts dict, cost_legend list).
    Result is cached in _DASH_CACHE so repeated hits (demo + session views) are instant.
    """
    ck = _cache_key(data_dir)
    if ck in _DASH_CACHE:
        return _DASH_CACHE[ck]

    if _has_extracted_json(data_dir):
        rev_df  = load_revenue_json(data_dir)
        cost_df = load_costs_json(data_dir)
        pl      = load_pl_json(data_dir)
        bs      = load_balance_sheet_json(data_dir)
    else:
        rev_df  = load_revenue(data_dir)
        cost_df = load_costs(data_dir)
        pl      = load_pl(data_dir)
        bs      = load_balance_sheet(data_dir)
        load_trial_balance(data_dir)  # data integrity check

    # ── Reconcile monthly revenue to P&L total ────────────────────────────────
    pl_revenue = pl.get("Total Revenue") or 0
    raw_total  = rev_df["Total"].sum()
    if raw_total > 0 and pl_revenue > 0:
        scale = pl_revenue / raw_total
        for col in [c for c in rev_df.columns if c != "Month"]:
            rev_df[col] = (rev_df[col] * scale).round(0).astype(int)

    # ── Dynamic column detection ──────────────────────────────────────────────
    channel_cols  = [c for c in rev_df.columns  if c not in ("Month", "Total")]
    cost_cat_cols = [c for c in cost_df.columns if c not in ("Month", "Total")]

    # ── KPIs ──────────────────────────────────────────────────────────────────
    total_revenue       = pl.get("Total Revenue",              0) or 0
    gross_profit        = pl.get("Gross Profit",               0) or 0
    ebitda              = pl.get("EBITDA",                     0) or 0
    net_profit          = pl.get("Net Profit After Tax",       0) or 0
    total_assets        = bs.get("TOTAL ASSETS",               0) or 0
    total_equity        = bs.get("Total Equity",               0) or 0
    current_assets      = bs.get("Total Current Assets",       0) or 0
    current_liabilities = bs.get("Total Current Liabilities",  0) or 0
    non_current_liab    = bs.get("Total Non-Current Liabilities", 0) or 0
    non_current_assets  = bs.get("Total Non-Current Assets",   0) or 0
    total_liabilities   = current_liabilities + non_current_liab

    gross_margin   = round(gross_profit / total_revenue * 100, 1) if total_revenue else 0
    net_margin     = round(net_profit   / total_revenue * 100, 1) if total_revenue else 0
    ebitda_margin  = round(ebitda       / total_revenue * 100, 1) if total_revenue else 0
    current_ratio  = round(current_assets / current_liabilities, 2) if current_liabilities else 0
    debt_to_equity = round(total_liabilities / total_equity, 2)    if total_equity         else 0

    # ── Depreciation & Amortisation (non-cash add-back for cash burn) ───────
    depreciation = 0
    for _plk, _plv in pl.items():
        _kl = str(_plk).strip().lower()
        if any(p in _kl for p in ('depreciation', 'amortisation', 'amortization',
                                   'd & a', 'd&a', ' da ')):
            try:
                _f = float(_plv)
                if _f != 0:
                    depreciation = abs(_f); break   # always treat as positive add-back
            except (TypeError, ValueError):
                pass

    # ── Cash on Hand (for runway calculator) ─────────────────────────────────
    cash = 0
    for _bsk, _bsv in bs.items():
        _kl = str(_bsk).strip().lower()
        if any(p in _kl for p in ('cash & cash equiv', 'cash and cash equiv',
                                   'cash at bank', 'cash on hand', 'cash balance')):
            try:
                cash = max(float(_bsv), 0); break
            except (TypeError, ValueError):
                pass
    if not cash:
        for _bsk, _bsv in bs.items():
            if str(_bsk).strip().lower() == 'cash':
                try:
                    cash = max(float(_bsv), 0)
                except (TypeError, ValueError):
                    pass
                break
    if not cash:
        for _bsk, _bsv in bs.items():
            if 'cash' in str(_bsk).lower():
                try:
                    _f = float(_bsv)
                    if _f > 0:
                        cash = _f; break
                except (TypeError, ValueError):
                    pass

    kpis = dict(
        total_revenue=total_revenue, gross_profit=gross_profit, gross_margin=gross_margin,
        ebitda=ebitda, ebitda_margin=ebitda_margin,
        net_profit=net_profit, net_margin=net_margin,
        current_ratio=current_ratio, debt_to_equity=debt_to_equity,
        total_assets=total_assets, total_equity=total_equity, total_liabilities=total_liabilities,
        current_assets=current_assets, current_liabilities=current_liabilities,
        cash=cash, depreciation=depreciation,
    )

    months = rev_df["Month"].tolist()

    # ── Chart 1: Revenue by channel (dynamic) ─────────────────────────────────
    fig_rev = go.Figure()
    for i, col in enumerate(channel_cols):
        color = PALETTE[i % len(PALETTE)]
        fig_rev.add_trace(go.Bar(
            name=col, x=months, y=rev_df[col].tolist(),
            marker_color=color, marker_line_width=0,
            hovertemplate=f"<b>%{{x}}</b><br>{col}: $%{{y:,.0f}}<extra></extra>",
        ))
    for month, total in zip(months, rev_df["Total"].tolist()):
        fig_rev.add_annotation(
            x=month, y=total,
            text=f"<b>${total/1e3:.0f}K</b>",
            showarrow=False, yanchor="bottom", yshift=5,
            font=dict(size=9, color=SLATE),
        )
    fig_rev.update_layout(
        **_layout("Monthly Revenue by Channel", y_title="Monthly Revenue ($)"),
        barmode="stack", bargap=0.35,
    )

    # ── Chart 2: Revenue vs Costs ─────────────────────────────────────────────
    fig_rvc = go.Figure()
    profit_monthly = [r - c for r, c in zip(rev_df["Total"].tolist(), cost_df["Total"].tolist())]
    for y_vals, color, name, dash in [
        (rev_df["Total"].tolist(),  NAVY, "Revenue",          "solid"),
        (cost_df["Total"].tolist(), RED,  "Total Costs",      "solid"),
        (profit_monthly,            TEAL, "Operating Profit", "dot"),
    ]:
        fig_rvc.add_trace(go.Scatter(
            x=months, y=y_vals, name=name, mode="lines+markers",
            line=dict(color=color, width=2.5, dash=dash),
            marker=dict(size=7, color=color, line=dict(color="white", width=1.5)),
            hovertemplate=f"<b>%{{x}}</b><br>{name}: $%{{y:,.0f}}<extra></extra>",
        ))
    rev_vals = rev_df["Total"].tolist()
    max_v, min_v = max(rev_vals), min(rev_vals)
    for month, val, label, color, ay in [
        (months[rev_vals.index(max_v)], max_v, f"Peak  ${max_v/1e3:.0f}K", GREEN, -36),
        (months[rev_vals.index(min_v)], min_v, f"Low  ${min_v/1e3:.0f}K",  RED,    36),
    ]:
        fig_rvc.add_annotation(
            x=month, y=val, text=label,
            showarrow=True, arrowhead=2, arrowsize=0.8, arrowwidth=1.5,
            arrowcolor=color, ax=0, ay=ay,
            font=dict(size=10, color=color),
            bgcolor="white", borderpad=3, bordercolor=color, borderwidth=1,
        )
    fig_rvc.update_layout(**_layout("Revenue vs Costs vs Operating Profit", y_title="Amount ($)"))

    # ── Chart 3: Cost Mix donut (dynamic) ─────────────────────────────────────
    cost_labels = cost_cat_cols
    cost_colors = [PALETTE[i % len(PALETTE)] for i in range(len(cost_cat_cols))]
    cost_vals   = [int(cost_df[c].sum()) for c in cost_cat_cols]
    total_costs = sum(cost_vals) or 1  # avoid div-by-zero

    fig_cost = go.Figure(go.Pie(
        labels=cost_labels, values=cost_vals,
        hole=0.54,
        marker=dict(colors=cost_colors, line=dict(color="white", width=2.5)),
        textinfo="none",
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f} · %{percent}<extra></extra>",
        sort=False, direction="clockwise",
    ))
    fig_cost.add_annotation(
        text=f"<b>{_fmt(total_costs)}</b><br>Total Costs",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=14, color=NAVY), align="center",
    )
    fig_cost.update_layout(
        paper_bgcolor="white", plot_bgcolor="white",
        font=_FONT, hoverlabel=_HOVER,
        title=dict(text="Cost Mix", font=dict(size=13, color="#1e293b"),
                   x=0, xanchor="left", pad=dict(l=8, t=4)),
        showlegend=False,
        margin=dict(l=24, r=24, t=48, b=16),
    )
    cost_legend = [
        {"label": label, "value": val,
         "pct": f"{val / total_costs * 100:.1f}%",
         "color": color}
        for label, val, color in zip(cost_labels, cost_vals, cost_colors)
    ]

    # ── Chart 4: Balance Sheet Composition ────────────────────────────────────
    fig_bs = go.Figure()
    for name, cat, val, color in [
        ("Current Assets",          "Assets",               current_assets,      TEAL2),
        ("Non-Current Assets",      "Assets",               non_current_assets,  NAVY),
        ("Current Liabilities",     "Liabilities & Equity", current_liabilities, RED),
        ("Non-Current Liabilities", "Liabilities & Equity", non_current_liab,    "#b91c1c"),
        ("Equity",                  "Liabilities & Equity", total_equity,        GREEN),
    ]:
        fig_bs.add_trace(go.Bar(
            name=name, x=[cat], y=[val],
            marker_color=color, marker_line_width=0,
            text=[f"${val:,.0f}"],
            textposition="inside", insidetextanchor="middle",
            textfont=dict(size=10, color="white"),
            constraintext="inside",
            hovertemplate=f"<b>{name}</b><br>${val:,.0f}<extra></extra>",
        ))
    fig_bs.update_layout(
        **_layout("Balance Sheet Composition", y_title="Amount ($)"),
        barmode="stack", bargap=0.5,
    )

    # ── Chart 5: Capital Structure horizontal stacked bar ────────────────────
    fig_capital = go.Figure()
    for name, val, color in [
        ("Equity",              total_equity,        GREEN),
        ("Long-term Debt",      non_current_liab,    "#b91c1c"),
        ("Current Liabilities", current_liabilities, RED),
    ]:
        fig_capital.add_trace(go.Bar(
            orientation="h",
            x=[val], y=[""],
            name=name,
            marker_color=color, marker_line_width=0,
            text=[f"${val:,.0f}"],
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=12, color="white", family="Inter, system-ui, sans-serif"),
            constraintext="inside",
            hovertemplate=f"<b>{name}</b><br>${val:,.0f}<extra></extra>",
        ))
    fig_capital.update_layout(
        paper_bgcolor="white", plot_bgcolor="white",
        font=_FONT, hoverlabel=_HOVER,
        title=dict(text="Capital Structure", font=dict(size=13, color="#1e293b"),
                   x=0, xanchor="left", pad=dict(l=8, t=4)),
        barmode="stack",
        bargap=0.55,
        showlegend=True,
        legend=dict(
            orientation="h", y=-0.12,
            font=dict(size=11, color=SLATE),
            bgcolor="rgba(0,0,0,0)", borderwidth=0,
        ),
        margin=dict(l=24, r=24, t=48, b=72),
        xaxis=dict(
            showgrid=True, gridcolor=GRID, gridwidth=1,
            tickprefix="$", tickformat=",.0f",
            tickfont=dict(size=10, color=SLATE),
            linecolor=BORDER, linewidth=1,
        ),
        yaxis=dict(
            showgrid=False, showticklabels=False,
            linecolor=BORDER, linewidth=1,
        ),
    )

    charts = dict(
        revenue=_json(fig_rev),
        rev_cost=_json(fig_rvc),
        cost_mix=_json(fig_cost),
        balance_sheet=_json(fig_bs),
        capital=_json(fig_capital),
    )

    _DASH_CACHE[ck] = (kpis, charts, cost_legend)
    return kpis, charts, cost_legend


# ── Routes ────────────────────────────────────────────────────────────────────

@main.route("/")
def index():
    return render_template("upload.html", slots=FILE_SLOTS)


@main.route("/upload", methods=["POST"])
def upload():
    session_id  = str(uuid.uuid4())[:8]
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    errors = []

    # ── Step 1: Save all files ────────────────────────────────────────────────
    for key, filename, label, _ncols, _hint in FILE_SLOTS:
        f = request.files.get(key)
        if not f or not f.filename:
            errors.append(f"Missing: {label}")
            continue
        if not f.filename.lower().endswith(".xlsx"):
            errors.append(f"{label} must be an .xlsx file")
            continue
        f.save(session_dir / filename)

    if errors:
        shutil.rmtree(session_dir, ignore_errors=True)
        return render_template("upload.html", slots=FILE_SLOTS, errors=errors)

    # ── Step 2: Go straight to dashboard (direct Excel loaders, no AI) ──────────
    # AI extraction is intentionally disabled in the upload flow — it caused
    # Render's 30-second request timeout to be hit before the response returned.
    # The loaders read column names directly from each file's header row, so
    # any reasonably formatted .xlsx works without needing Claude.
    return redirect(url_for("main.dashboard", session_id=session_id))


@main.route("/confirm/<session_id>")
def confirm(session_id):
    session_dir = UPLOAD_DIR / session_id
    if not session_dir.exists():
        return redirect(url_for("main.index"))

    summary_path = session_dir / "extraction_summary.json"
    if not summary_path.exists():
        # No AI summary — skip straight to dashboard (shouldn't normally happen)
        return redirect(url_for("main.dashboard", session_id=session_id))

    summary = json.loads(summary_path.read_text())
    return render_template("confirm.html", summary=summary, session_id=session_id)


@main.route("/dashboard/<session_id>")
def dashboard(session_id):
    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return redirect(url_for("main.index"))
        data_dir = session_dir

    try:
        kpis, charts, cost_legend = _build_dashboard_data(data_dir)
    except Exception as e:
        return render_template("upload.html", slots=FILE_SLOTS,
                               errors=[f"Could not generate dashboard: {e}"])

    return render_template("dashboard.html", kpis=kpis, charts=charts,
                           cost_legend=cost_legend, session_id=session_id)


@main.route("/api/refresh/<session_id>")
def refresh(session_id):
    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return jsonify({"error": "Session not found — please re-upload your files."}), 404
        data_dir = session_dir

    try:
        kpis, charts, cost_legend = _build_dashboard_data(data_dir)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    charts_obj = {k: json.loads(v) for k, v in charts.items()}
    return jsonify({"kpis": kpis, "charts": charts_obj, "cost_legend": cost_legend})


@main.route("/api/insights/<session_id>")
def api_insights(session_id):
    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return jsonify({"error": "Session not found"}), 404
        data_dir = session_dir

    try:
        kpis, _, _ = _build_dashboard_data(data_dir)
    except Exception as e:
        return jsonify({"error": str(e), "insights": []}), 500

    try:
        from .insights import get_insights
        insights, err = get_insights(data_dir, kpis)
        return jsonify({"insights": insights, "error": err})
    except Exception as e:
        return jsonify({"error": str(e), "insights": []}), 500


@main.route("/export-pdf/<session_id>")
def export_pdf(session_id):
    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return redirect(url_for("main.index"))
        data_dir = session_dir

    try:
        kpis, _, _ = _build_dashboard_data(data_dir)
    except Exception as e:
        return f"Could not load dashboard data: {e}", 500

    # Load raw DataFrames for the tables (use AI-extracted JSON when available)
    try:
        if _has_extracted_json(data_dir):
            from .loader import load_revenue_json, load_costs_json, load_pl_json, load_balance_sheet_json
            rev_df  = load_revenue_json(data_dir)
            cost_df = load_costs_json(data_dir)
            pl      = load_pl_json(data_dir)
            bs      = load_balance_sheet_json(data_dir)
        else:
            rev_df  = load_revenue(data_dir)
            cost_df = load_costs(data_dir)
            pl      = load_pl(data_dir)
            bs      = load_balance_sheet(data_dir)
    except Exception as e:
        return f"Could not load financial data: {e}", 500

    # AI bullets — use cache only (instant), never block on a fresh Claude call
    ai_bullets = None
    try:
        from .insights import get_ai_bullets
        ai_bullets, _ = get_ai_bullets(data_dir, kpis)
    except Exception:
        pass

    # Build PDF
    try:
        from .pdf_export import build_cfo_pdf
        buf = build_cfo_pdf(kpis, rev_df, cost_df, pl, bs, ai_bullets)
    except Exception as e:
        return f"PDF generation failed: {e}", 500

    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="GrainCo_CFO_Report_FY2025.pdf",
    )


# ── Financial context builder (used by /api/ask) ─────────────────────────────

def _build_financial_context(data_dir, kpis, cost_legend) -> str:
    """
    Assemble a structured plain-text summary of all financial data so Claude
    can answer natural-language questions with specific numbers.
    """
    lines = []

    # ── KPIs ─────────────────────────────────────────────────────────────────
    lines += [
        "=== KEY PERFORMANCE INDICATORS (FY2025) ===",
        f"  Total Revenue:          ${kpis.get('total_revenue',     0):>14,.0f}",
        f"  Gross Profit:           ${kpis.get('gross_profit',      0):>14,.0f}  ({kpis.get('gross_margin',  0):.1f}% margin)",
        f"  EBITDA:                 ${kpis.get('ebitda',            0):>14,.0f}  ({kpis.get('ebitda_margin', 0):.1f}% margin)",
        f"  Net Profit After Tax:   ${kpis.get('net_profit',        0):>14,.0f}  ({kpis.get('net_margin',    0):.1f}% margin)",
        f"  Total Assets:           ${kpis.get('total_assets',      0):>14,.0f}",
        f"  Total Equity:           ${kpis.get('total_equity',      0):>14,.0f}",
        f"  Total Liabilities:      ${kpis.get('total_liabilities', 0):>14,.0f}",
        f"  Current Ratio:          {kpis.get('current_ratio',      0):>13.2f}x",
        f"  Debt/Equity Ratio:      {kpis.get('debt_to_equity',     0):>13.2f}x",
        "",
    ]

    # ── Monthly revenue ───────────────────────────────────────────────────────
    try:
        if _has_extracted_json(data_dir):
            rev_df = load_revenue_json(data_dir)
        else:
            rev_df = load_revenue(data_dir)
        ch_cols = [c for c in rev_df.columns if c not in ("Month", "Total")]
        lines.append("=== MONTHLY REVENUE ===")
        lines.append("  " + f"{'Month':<8}" + "".join(f"  {c:>12}" for c in ch_cols) + f"  {'Total':>12}")
        for _, row in rev_df.iterrows():
            r = "  " + f"{str(row['Month']):<8}"
            r += "".join(f"  ${row.get(c, 0):>11,.0f}" for c in ch_cols)
            r += f"  ${row.get('Total', 0):>11,.0f}"
            lines.append(r)
        lines.append("")
    except Exception:
        pass

    # ── Annual cost breakdown ─────────────────────────────────────────────────
    if cost_legend:
        lines.append("=== ANNUAL COST BREAKDOWN ===")
        for item in cost_legend:
            lines.append(f"  {item['label']:<22} ${item['value']:>12,.0f}   ({item['pct']})")
        lines.append("")

    # ── Monthly costs ─────────────────────────────────────────────────────────
    try:
        if _has_extracted_json(data_dir):
            cost_df = load_costs_json(data_dir)
        else:
            cost_df = load_costs(data_dir)
        cost_cols = [c for c in cost_df.columns if c not in ("Month", "Total")]
        lines.append("=== MONTHLY COSTS ===")
        lines.append("  " + f"{'Month':<8}" + "".join(f"  {c:>12}" for c in cost_cols) + f"  {'Total':>12}")
        for _, row in cost_df.iterrows():
            r = "  " + f"{str(row['Month']):<8}"
            r += "".join(f"  ${row.get(c, 0):>11,.0f}" for c in cost_cols)
            r += f"  ${row.get('Total', 0):>11,.0f}"
            lines.append(r)
        lines.append("")
    except Exception:
        pass

    # ── Balance sheet ─────────────────────────────────────────────────────────
    try:
        if _has_extracted_json(data_dir):
            bs = load_balance_sheet_json(data_dir)
        else:
            bs = load_balance_sheet(data_dir)
        lines.append("=== BALANCE SHEET (31 Dec 2025) ===")
        for item, amount in bs.items():
            try:
                if abs(float(amount)) > 0:
                    lines.append(f"  {str(item):<38} ${float(amount):>12,.0f}")
            except (TypeError, ValueError):
                pass
        lines.append("")
    except Exception:
        pass

    return "\n".join(lines)


# ── System prompt for the ask endpoint ───────────────────────────────────────
_ASK_SYSTEM = (
    "You are a senior CFO advisor with deep expertise in financial analysis. "
    "You have been given a company's complete financial data. "
    "Answer the user's question in 2-3 sentences maximum. "
    "Be specific, reference actual numbers, and be actionable. "
    "If the question cannot be answered from the data provided, say so clearly. "
    "Never make up numbers."
)


@main.route("/api/ask/<session_id>", methods=["POST"])
def api_ask(session_id):
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return jsonify({"error": "Session not found"}), 404
        data_dir = session_dir

    try:
        kpis, _, cost_legend = _build_dashboard_data(data_dir)
    except Exception as e:
        return jsonify({"error": f"Could not load data: {e}"}), 500

    context = _build_financial_context(data_dir, kpis, cost_legend)

    def generate():
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            yield f"data: {json.dumps({'error': 'ANTHROPIC_API_KEY not configured'})}\n\n"
            return
        try:
            import anthropic
        except ImportError:
            yield f"data: {json.dumps({'error': 'anthropic package not installed'})}\n\n"
            return

        client = anthropic.Anthropic(api_key=api_key)

        # Discover best model
        try:
            from .insights import _best_model
            model_id, err = _best_model(client)
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        if not model_id:
            yield f"data: {json.dumps({'error': err})}\n\n"
            return

        try:
            with client.messages.stream(
                model=model_id,
                max_tokens=256,
                system=_ASK_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": f"Financial data:\n\n{context}\n\nQuestion: {question}",
                }],
            ) as stream:
                for text in stream.text_stream:
                    yield f"data: {json.dumps({'text': text})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': f'Claude error: {exc}'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",   # disable Nginx/Render proxy buffering
        },
    )


# ── System prompt for the monthly board report ───────────────────────────────
_REPORT_SYSTEM = (
    "You are a CFO writing a monthly financial report for the board of directors.\n\n"
    "Output ONLY the report body as HTML — no <html>, <head>, or <body> tags, no markdown fences.\n\n"
    "Use this exact structure for every section:\n"
    "<div class=\"rpt-section\">\n"
    "  <h2>Section Title</h2>\n"
    "  <p>Content</p>\n"
    "</div>\n\n"
    "Required sections in this order:\n"
    "1. Executive Summary — 2-3 sentences covering overall financial health and key highlights\n"
    "2. Revenue Performance — analysis of revenue, channels, and trends; reference actual figures\n"
    "3. Cost Analysis — breakdown of costs, efficiency, and any concerns\n"
    "4. Profitability — gross profit, EBITDA, and net profit with margins\n"
    "5. Balance Sheet Health — liquidity, solvency, current ratio, debt/equity\n"
    "6. Cash Position — cash on hand, monthly burn rate or cash generation, runway\n"
    "7. Key Risks — use <ul><li> for exactly 2-3 bullet points of financial risks\n"
    "8. Recommended Actions — use <ul><li> for exactly 2-3 actionable bullet points\n\n"
    "Rules:\n"
    "- Be specific — reference actual numbers from the data provided\n"
    "- Use professional CFO language suitable for a board audience\n"
    "- Keep each narrative section to 3-4 sentences maximum\n"
    "- Wrap all key numbers in <strong> tags\n"
    "- Do not add any text outside the section divs"
)


@main.route("/api/monthly-report/<session_id>", methods=["POST"])
def api_monthly_report(session_id):
    from datetime import date as _date

    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return jsonify({"error": "Session not found"}), 404
        data_dir = session_dir

    try:
        kpis, _, cost_legend = _build_dashboard_data(data_dir)
    except Exception as exc:
        return jsonify({"error": f"Could not load data: {exc}"}), 500

    context = _build_financial_context(data_dir, kpis, cost_legend)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 503

    try:
        import anthropic
    except ImportError:
        return jsonify({"error": "anthropic package not installed"}), 503

    client = anthropic.Anthropic(api_key=api_key)

    try:
        from .insights import _best_model
        model_id, err = _best_model(client)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    if not model_id:
        return jsonify({"error": err}), 503

    try:
        message = client.messages.create(
            model=model_id,
            max_tokens=2000,
            system=_REPORT_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Financial data:\n\n{context}\n\n"
                    "Generate the monthly board report now."
                ),
            }],
        )
        report_html = message.content[0].text
    except Exception as exc:
        return jsonify({"error": f"Claude error: {exc}"}), 500

    return jsonify({
        "report_html":  report_html,
        "generated_at": _date.today().strftime("%B %Y"),
        "company":      "Grain & Co.",
    })


@main.route("/api/ai-insights/<session_id>")
def api_ai_insights(session_id):
    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return jsonify({"bullets": None, "error": "Session not found"})
        data_dir = session_dir

    try:
        kpis, _, _ = _build_dashboard_data(data_dir)
    except Exception as e:
        return jsonify({"bullets": None, "error": f"KPI load failed: {e}"})

    try:
        from .insights import get_ai_bullets
        bullets, err = get_ai_bullets(data_dir, kpis)
        return jsonify({"bullets": bullets, "error": err})
    except Exception as e:
        return jsonify({"bullets": None, "error": str(e)})
