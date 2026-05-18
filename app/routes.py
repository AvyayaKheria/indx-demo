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

# Prior-year upload slots — same structure, all optional
FILE_SLOTS_PY = [
    ("revenue_py",       "revenue.xlsx",       "Revenue (FY2024)",          5,
     "Month, Dine-In, Delivery, Catering, Total"),
    ("costs_py",         "costs.xlsx",         "Costs (FY2024)",            6,
     "Month, COGS, Payroll, Rent, Marketing, Total"),
    ("pl_py",            "pl.xlsx",            "P&L Statement (FY2024)",    2,
     "Item, Amount"),
    ("balance_sheet_py", "balance_sheet.xlsx", "Balance Sheet (FY2024)",    2,
     "Item, Amount"),
    ("trial_balance_py", "trial_balance.xlsx", "Trial Balance (FY2024)",    3,
     "Account, Debit, Credit"),
]

# Budget upload slots — optional, same structure
FILE_SLOTS_BUD = [
    ("revenue_bud",       "revenue.xlsx",       "Revenue Budget",       5,
     "Month, Dine-In, Delivery, Catering, Total"),
    ("costs_bud",         "costs.xlsx",         "Costs Budget",         6,
     "Month, COGS, Payroll, Rent, Marketing, Total"),
    ("pl_bud",            "pl.xlsx",            "P&L Budget",           2,
     "Item, Amount"),
    ("balance_sheet_bud", "balance_sheet.xlsx", "Balance Sheet Budget", 2,
     "Item, Amount"),
    ("trial_balance_bud", "trial_balance.xlsx", "Trial Balance Budget", 3,
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

NAVY_LIGHT = "#6b8cc9"   # prior-year bar / line colour
RED_LIGHT  = "#f87171"   # prior-year cost line colour

# ── Month helpers (used by forecast) ─────────────────────────────────────────
_MONTH_NUM   = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
_MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]

def _month_to_num(s: str) -> int:
    """'Jan', 'Jan-25', 'January' … → 1-12, or 0 if unrecognised."""
    return _MONTH_NUM.get(str(s).lower().strip()[:3], 0)


def _statistical_forecast(rev_df, n: int = 6):
    """
    Pure-Python statistical 6-month revenue forecast.
    Returns (forecasts, confidence_level, avg_growth_pct) where
    forecasts is a list of n dicts: {month_label, month_num, base, low, high}.
    """
    totals = [float(v) for v in rev_df["Total"].tolist()]
    months = rev_df["Month"].tolist()
    k = len(totals)
    if k == 0:
        return [], "Low", 0.0

    # Median MoM growth (robust to outliers)
    growth_rates = [(totals[i] - totals[i-1]) / totals[i-1]
                    for i in range(1, k) if totals[i-1] > 0]
    if growth_rates:
        sg = sorted(growth_rates)
        mid = len(sg) // 2
        avg_growth = sg[mid] if len(sg) % 2 else (sg[mid-1] + sg[mid]) / 2.0
    else:
        avg_growth = 0.0

    # Seasonality: each month's ratio relative to overall mean
    overall_mean = sum(totals) / k if k else 1.0
    seas: dict = {}
    for i, m in enumerate(months):
        mn = _month_to_num(m)
        if mn:
            seas.setdefault(mn, []).append(totals[i] / overall_mean if overall_mean > 0 else 1.0)
    avg_season = {mn: sum(v) / len(v) for mn, v in seas.items()}

    # Confidence based on coefficient of variation
    if overall_mean > 0 and k > 1:
        variance = sum((t - overall_mean) ** 2 for t in totals) / k
        cv = variance ** 0.5 / overall_mean
    else:
        cv = 0.5

    if cv < 0.12:
        confidence, band = "High",   0.08
    elif cv < 0.25:
        confidence, band = "Medium", 0.16
    else:
        confidence, band = "Low",    0.28

    last_num    = _month_to_num(months[-1]) or 12
    last_val    = totals[-1]
    last_season = avg_season.get(last_num, 1.0)

    results = []
    for i in range(1, n + 1):
        fwd_num    = (last_num - 1 + i) % 12 + 1
        trend      = (1 + avg_growth) ** i
        fwd_season = avg_season.get(fwd_num, 1.0)
        season_adj = fwd_season / last_season if last_season else 1.0
        base = last_val * trend * season_adj
        results.append({
            "month_label": f"{_MONTH_NAMES[fwd_num - 1]} 26",
            "month_num":   fwd_num,
            "base": round(base),
            "low":  round(base * (1 - band)),
            "high": round(base * (1 + band)),
            "reasoning": "",
        })

    return results, confidence, round(avg_growth * 100, 1)

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
_PY_CACHE:   dict = {}   # key: str(data_dir) → (kpis_py, charts_compare, cost_legend_py)
_BUD_CACHE:  dict = {}   # key: str(data_dir)+"__budget__" → (pl_bud, rev_df_bud, cost_df_bud, charts_budget)


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


# ── Demo prior-year generator ─────────────────────────────────────────────────

def _generate_demo_prior_year(rev_df_cy, cost_df_cy, pl_cy, bs_cy):
    """Return (rev_df_py, cost_df_py, pl_py, bs_py) as synthetic FY2024 data."""
    # Revenue: 82 % of FY2025
    rev_df_py = rev_df_cy.copy()
    for col in [c for c in rev_df_py.columns if c != "Month"]:
        rev_df_py[col] = (rev_df_py[col] * 0.82).round(0).astype(int)

    # Costs: per-category ratios
    cost_df_py = cost_df_cy.copy()
    _COST_RATIOS = {
        ('cogs', 'cost of good', 'cost of sale'): 0.84,
        ('payroll', 'wage', 'staff', 'salary', 'labour', 'labor'): 0.91,
        ('rent', 'lease', 'occupancy'): 1.00,
        ('marketing', 'advertis', 'promo'): 0.88,
    }
    for col in [c for c in cost_df_py.columns if c != "Month"]:
        cl = col.lower()
        ratio = 0.87   # default
        for keys, r in _COST_RATIOS.items():
            if any(k in cl for k in keys):
                ratio = r
                break
        cost_df_py[col] = (cost_df_py[col] * ratio).round(0).astype(int)

    # P&L: 82 %
    pl_py = {}
    for k, v in pl_cy.items():
        try:
            pl_py[k] = round(float(v) * 0.82, 0)
        except (TypeError, ValueError):
            pl_py[k] = v

    # Balance sheet: 85 %
    bs_py = {}
    for k, v in bs_cy.items():
        try:
            bs_py[k] = round(float(v) * 0.85, 0)
        except (TypeError, ValueError):
            bs_py[k] = v

    return rev_df_py, cost_df_py, pl_py, bs_py


# ── Prior-year data builder ───────────────────────────────────────────────────

def _build_prior_year_data(data_dir, prior_year_dir):
    """
    Load or generate FY2024 data and build comparison charts.
    Returns (kpis_py dict, charts_compare dict, cost_legend_py list).
    Result is cached in _PY_CACHE.
    """
    ck = _cache_key(data_dir)
    if ck in _PY_CACHE:
        return _PY_CACHE[ck]

    # ── Load current-year raw data ────────────────────────────────────────
    if _has_extracted_json(data_dir):
        rev_df_cy  = load_revenue_json(data_dir)
        cost_df_cy = load_costs_json(data_dir)
        pl_cy      = load_pl_json(data_dir)
        bs_cy      = load_balance_sheet_json(data_dir)
    else:
        rev_df_cy  = load_revenue(data_dir)
        cost_df_cy = load_costs(data_dir)
        pl_cy      = load_pl(data_dir)
        bs_cy      = load_balance_sheet(data_dir)

    # Reconcile current-year revenue (same logic as _build_dashboard_data)
    pl_rev_cy = pl_cy.get("Total Revenue") or 0
    raw_cy    = rev_df_cy["Total"].sum()
    if raw_cy > 0 and pl_rev_cy > 0:
        s = pl_rev_cy / raw_cy
        for col in [c for c in rev_df_cy.columns if c != "Month"]:
            rev_df_cy[col] = (rev_df_cy[col] * s).round(0).astype(int)

    # ── Load or generate prior-year raw data ─────────────────────────────
    if prior_year_dir and Path(prior_year_dir).exists():
        pyd = Path(prior_year_dir)
        if _has_extracted_json(pyd):
            rev_df_py  = load_revenue_json(pyd)
            cost_df_py = load_costs_json(pyd)
            pl_py      = load_pl_json(pyd)
            bs_py      = load_balance_sheet_json(pyd)
        else:
            rev_df_py  = load_revenue(pyd)
            cost_df_py = load_costs(pyd)
            pl_py      = load_pl(pyd)
            bs_py      = load_balance_sheet(pyd)
    else:
        rev_df_py, cost_df_py, pl_py, bs_py = _generate_demo_prior_year(
            rev_df_cy, cost_df_cy, pl_cy, bs_cy
        )

    # Reconcile prior-year revenue
    pl_rev_py = pl_py.get("Total Revenue") or 0
    raw_py    = rev_df_py["Total"].sum()
    if raw_py > 0 and pl_rev_py > 0:
        s = pl_rev_py / raw_py
        for col in [c for c in rev_df_py.columns if c != "Month"]:
            rev_df_py[col] = (rev_df_py[col] * s).round(0).astype(int)

    # ── Prior-year KPIs ───────────────────────────────────────────────────
    total_revenue_py       = pl_py.get("Total Revenue",              0) or 0
    gross_profit_py        = pl_py.get("Gross Profit",               0) or 0
    ebitda_py              = pl_py.get("EBITDA",                     0) or 0
    net_profit_py          = pl_py.get("Net Profit After Tax",       0) or 0
    total_assets_py        = bs_py.get("TOTAL ASSETS",               0) or 0
    total_equity_py        = bs_py.get("Total Equity",               0) or 0
    current_assets_py      = bs_py.get("Total Current Assets",       0) or 0
    current_liab_py        = bs_py.get("Total Current Liabilities",  0) or 0
    non_curr_liab_py       = bs_py.get("Total Non-Current Liabilities", 0) or 0
    non_curr_assets_py     = bs_py.get("Total Non-Current Assets",   0) or 0
    total_liab_py          = current_liab_py + non_curr_liab_py

    gross_margin_py   = round(gross_profit_py / total_revenue_py * 100, 1) if total_revenue_py else 0
    net_margin_py     = round(net_profit_py   / total_revenue_py * 100, 1) if total_revenue_py else 0
    ebitda_margin_py  = round(ebitda_py       / total_revenue_py * 100, 1) if total_revenue_py else 0
    current_ratio_py  = round(current_assets_py / current_liab_py, 2) if current_liab_py else 0
    debt_to_equity_py = round(total_liab_py / total_equity_py, 2) if total_equity_py else 0

    kpis_py = dict(
        total_revenue=total_revenue_py, gross_profit=gross_profit_py, gross_margin=gross_margin_py,
        ebitda=ebitda_py, ebitda_margin=ebitda_margin_py,
        net_profit=net_profit_py, net_margin=net_margin_py,
        current_ratio=current_ratio_py, debt_to_equity=debt_to_equity_py,
        total_assets=total_assets_py, total_equity=total_equity_py,
        total_liabilities=total_liab_py,
        current_assets=current_assets_py, current_liabilities=current_liab_py,
    )

    # ── Prior-year cost legend ────────────────────────────────────────────
    cost_cat_cols_py = [c for c in cost_df_py.columns if c not in ("Month", "Total")]
    cost_colors_py   = [PALETTE[i % len(PALETTE)] for i in range(len(cost_cat_cols_py))]
    cost_vals_py     = [int(cost_df_py[c].sum()) for c in cost_cat_cols_py]
    total_costs_py   = sum(cost_vals_py) or 1
    cost_legend_py   = [
        {"label": lbl, "value": val,
         "pct": f"{val / total_costs_py * 100:.1f}%",
         "color": col}
        for lbl, val, col in zip(cost_cat_cols_py, cost_vals_py, cost_colors_py)
    ]

    # ── Comparison charts ─────────────────────────────────────────────────
    months = rev_df_cy["Month"].tolist()

    # Chart A: Revenue grouped bars (total per month, two bars)
    fig_rev_cmp = go.Figure()
    fig_rev_cmp.add_trace(go.Bar(
        name="FY2025", x=months, y=rev_df_cy["Total"].tolist(),
        marker_color=NAVY, marker_line_width=0,
        hovertemplate="<b>%{x}</b><br>FY2025: $%{y:,.0f}<extra></extra>",
    ))
    fig_rev_cmp.add_trace(go.Bar(
        name="FY2024", x=months, y=rev_df_py["Total"].tolist(),
        marker_color=NAVY_LIGHT, marker_line_width=0,
        hovertemplate="<b>%{x}</b><br>FY2024: $%{y:,.0f}<extra></extra>",
    ))
    fig_rev_cmp.update_layout(
        **_layout("Monthly Revenue: FY2025 vs FY2024", y_title="Monthly Revenue ($)"),
        barmode="group", bargap=0.2, bargroupgap=0.06,
    )

    # Chart B: Rev vs Costs — four lines
    fig_rvc_cmp = go.Figure()
    for y_vals, color, name, dash in [
        (rev_df_cy["Total"].tolist(),  NAVY,       "FY2025 Revenue", "solid"),
        (rev_df_py["Total"].tolist(),  NAVY_LIGHT, "FY2024 Revenue", "dash"),
        (cost_df_cy["Total"].tolist(), RED,        "FY2025 Costs",   "solid"),
        (cost_df_py["Total"].tolist(), RED_LIGHT,  "FY2024 Costs",   "dash"),
    ]:
        fig_rvc_cmp.add_trace(go.Scatter(
            x=months, y=y_vals, name=name, mode="lines+markers",
            line=dict(color=color, width=2.5, dash=dash),
            marker=dict(size=6, color=color, line=dict(color="white", width=1.5)),
            hovertemplate=f"<b>%{{x}}</b><br>{name}: $%{{y:,.0f}}<extra></extra>",
        ))
    fig_rvc_cmp.update_layout(**_layout("Revenue & Costs: FY2025 vs FY2024", y_title="Amount ($)"))

    # Chart C: Balance Sheet grouped bars (Assets / Liabilities / Equity)
    bs_labels   = ["Total Assets", "Total Liabilities", "Total Equity"]
    bs_cy_vals  = [
        bs_cy.get("TOTAL ASSETS", 0) or 0,
        (bs_cy.get("Total Current Liabilities", 0) or 0) + (bs_cy.get("Total Non-Current Liabilities", 0) or 0),
        bs_cy.get("Total Equity", 0) or 0,
    ]
    bs_py_vals_ = [total_assets_py, total_liab_py, total_equity_py]
    bs_bar_colors = [TEAL, RED, GREEN]

    fig_bs_cmp = go.Figure()
    for cy_v, py_v, lbl, col in zip(bs_cy_vals, bs_py_vals_, bs_labels, bs_bar_colors):
        fig_bs_cmp.add_trace(go.Bar(
            name=f"FY2025 {lbl}", x=[lbl], y=[cy_v],
            marker_color=col, marker_line_width=0,
            text=[f"${cy_v:,.0f}"], textposition="outside",
            textfont=dict(size=9), constraintext="none",
            hovertemplate=f"<b>{lbl}</b><br>FY2025: $%{{y:,.0f}}<extra></extra>",
            legendgroup=lbl, showlegend=True,
        ))
        fig_bs_cmp.add_trace(go.Bar(
            name=f"FY2024 {lbl}", x=[lbl], y=[py_v],
            marker_color=col, marker_line_width=0, opacity=0.45,
            text=[f"${py_v:,.0f}"], textposition="outside",
            textfont=dict(size=9), constraintext="none",
            hovertemplate=f"<b>{lbl}</b><br>FY2024: $%{{y:,.0f}}<extra></extra>",
            legendgroup=lbl, legendgrouptitle_text=lbl,
        ))
    fig_bs_cmp.update_layout(
        **_layout("Balance Sheet: FY2025 vs FY2024", y_title="Amount ($)"),
        barmode="group", bargap=0.3, bargroupgap=0.05,
        showlegend=False,
    )

    # Chart D: Cost mix side-by-side donuts
    cost_cat_cols_cy = [c for c in cost_df_cy.columns if c not in ("Month", "Total")]
    cost_colors_cy   = [PALETTE[i % len(PALETTE)] for i in range(len(cost_cat_cols_cy))]
    cost_vals_cy_    = [int(cost_df_cy[c].sum()) for c in cost_cat_cols_cy]

    # Align prior-year columns to current-year so colours match
    common_labels = cost_cat_cols_cy  # use CY labels as canonical
    py_vals_aligned = []
    for lbl in common_labels:
        # find matching column in prior year (case-insensitive)
        match = next((c for c in cost_cat_cols_py if c.lower() == lbl.lower()), None)
        if match:
            py_vals_aligned.append(int(cost_df_py[match].sum()))
        else:
            py_vals_aligned.append(0)

    fig_cost_cmp = go.Figure()
    fig_cost_cmp.add_trace(go.Pie(
        labels=common_labels, values=cost_vals_cy_,
        hole=0.45,
        marker=dict(colors=cost_colors_cy, line=dict(color="white", width=2)),
        textinfo="none",
        domain=dict(x=[0, 0.46]),
        name="FY2025",
        title=dict(text="FY2025", font=dict(size=12, color=NAVY)),
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f} · %{percent}<extra></extra>",
    ))
    fig_cost_cmp.add_trace(go.Pie(
        labels=common_labels, values=py_vals_aligned,
        hole=0.45,
        marker=dict(colors=cost_colors_cy, line=dict(color="white", width=2)),
        textinfo="none",
        domain=dict(x=[0.54, 1.0]),
        name="FY2024",
        title=dict(text="FY2024", font=dict(size=12, color=NAVY_LIGHT)),
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f} · %{percent}<extra></extra>",
    ))
    fig_cost_cmp.update_layout(
        paper_bgcolor="white", plot_bgcolor="white",
        font=_FONT, hoverlabel=_HOVER,
        title=dict(text="Cost Mix: FY2025 vs FY2024",
                   font=dict(size=13, color="#1e293b"),
                   x=0, xanchor="left", pad=dict(l=8, t=4)),
        showlegend=False,
        margin=dict(l=24, r=24, t=48, b=16),
    )

    charts_compare = dict(
        revenue=_json(fig_rev_cmp),
        rev_cost=_json(fig_rvc_cmp),
        balance_sheet=_json(fig_bs_cmp),
        cost_mix=_json(fig_cost_cmp),
    )

    result = (kpis_py, charts_compare, cost_legend_py)
    _PY_CACHE[ck] = result
    return result


# ── Demo budget generator ─────────────────────────────────────────────────────

def _generate_demo_budget(rev_df_cy, cost_df_cy, pl_cy):
    """Return (rev_df_bud, cost_df_bud, pl_bud) as synthetic FY2025 budget data.
    Budget was slightly optimistic — creates realistic variances for demo."""
    # Revenue: budget was 8% higher than actual (optimistic planning)
    rev_df_bud = rev_df_cy.copy()
    for col in [c for c in rev_df_bud.columns if c != "Month"]:
        rev_df_bud[col] = (rev_df_bud[col] * 1.08).round(0).astype(int)

    # Costs: per-category budget ratios vs actual
    cost_df_bud = cost_df_cy.copy()
    _BUD_COST_RATIOS = {
        ('cogs', 'cost of good', 'cost of sale'): 0.95,       # actual 5% over budget
        ('payroll', 'wage', 'staff', 'salary', 'labour', 'labor'): 0.97,  # actual 3% over
        ('rent', 'lease', 'occupancy'): 1.00,                  # on budget
        ('marketing', 'advertis', 'promo'): 1.10,              # actual 10% under budget
    }
    for col in [c for c in cost_df_bud.columns if c != "Month"]:
        cl = col.lower()
        ratio = 0.96   # default: actuals slightly above budget
        for keys, r in _BUD_COST_RATIOS.items():
            if any(k in cl for k in keys):
                ratio = r
                break
        cost_df_bud[col] = (cost_df_bud[col] * ratio).round(0).astype(int)

    # P&L budget
    pl_bud = {}
    for k, v in pl_cy.items():
        try:
            fv = float(v)
            kl = k.lower()
            if any(x in kl for x in ('total revenue', 'revenue', 'sales')):
                ratio = 1.08
            elif any(x in kl for x in ('net profit', 'npat', 'profit after tax')):
                ratio = 1.20   # budgeted higher profit
            elif any(x in kl for x in ('ebitda',)):
                ratio = 1.15
            elif any(x in kl for x in ('gross profit',)):
                ratio = 1.10
            elif any(x in kl for x in ('cogs', 'cost of good', 'cost of sale')):
                ratio = 0.95 if fv > 0 else 1.05
            elif any(x in kl for x in ('payroll', 'wage', 'staff', 'salary', 'labour')):
                ratio = 0.97 if fv > 0 else 1.03
            elif any(x in kl for x in ('marketing', 'advertis', 'promo')):
                ratio = 1.10 if fv > 0 else 0.90
            elif any(x in kl for x in ('rent', 'lease', 'occupancy')):
                ratio = 1.00
            else:
                ratio = 1.0
            pl_bud[k] = round(fv * ratio, 0)
        except (TypeError, ValueError):
            pl_bud[k] = v

    return rev_df_bud, cost_df_bud, pl_bud


# ── Budget vs Actuals data builder ────────────────────────────────────────────

def _build_budget_data(data_dir, budget_dir):
    """
    Load or generate FY2025 budget data and build comparison chart.
    Returns (pl_bud, rev_df_bud, cost_df_bud, charts_budget).
    """
    ck = _cache_key(data_dir) + "__budget__"
    if ck in _BUD_CACHE:
        return _BUD_CACHE[ck]

    # Load current-year raw data
    if _has_extracted_json(data_dir):
        rev_df_cy  = load_revenue_json(data_dir)
        cost_df_cy = load_costs_json(data_dir)
        pl_cy      = load_pl_json(data_dir)
    else:
        rev_df_cy  = load_revenue(data_dir)
        cost_df_cy = load_costs(data_dir)
        pl_cy      = load_pl(data_dir)

    # Reconcile CY revenue to P&L total
    pl_rev_cy = pl_cy.get("Total Revenue") or 0
    raw_cy = rev_df_cy["Total"].sum()
    if raw_cy > 0 and pl_rev_cy > 0:
        s = pl_rev_cy / raw_cy
        for col in [c for c in rev_df_cy.columns if c != "Month"]:
            rev_df_cy[col] = (rev_df_cy[col] * s).round(0).astype(int)

    # Load or generate budget data
    if budget_dir and Path(budget_dir).exists():
        bd = Path(budget_dir)
        if _has_extracted_json(bd):
            rev_df_bud  = load_revenue_json(bd)
            cost_df_bud = load_costs_json(bd)
            pl_bud      = load_pl_json(bd)
        else:
            rev_df_bud  = load_revenue(bd)
            cost_df_bud = load_costs(bd)
            pl_bud      = load_pl(bd)
    else:
        rev_df_bud, cost_df_bud, pl_bud = _generate_demo_budget(rev_df_cy, cost_df_cy, pl_cy)

    # ── Monthly Revenue BvA chart (grouped bars + variance line) ─────────────
    months        = rev_df_cy["Month"].tolist()
    actual_totals = [int(v) for v in rev_df_cy["Total"].tolist()]
    budget_totals = [int(v) for v in rev_df_bud["Total"].tolist()]
    variances     = [a - b for a, b in zip(actual_totals, budget_totals)]
    var_colors    = [GREEN if v >= 0 else RED for v in variances]

    fig_bva = go.Figure()
    fig_bva.add_trace(go.Bar(
        name="Actual", x=months, y=actual_totals,
        marker_color=NAVY, marker_line_width=0,
        hovertemplate="<b>%{x}</b><br>Actual: $%{y:,.0f}<extra></extra>",
    ))
    fig_bva.add_trace(go.Bar(
        name="Budget", x=months, y=budget_totals,
        marker_color=TEAL, marker_line_width=0,
        hovertemplate="<b>%{x}</b><br>Budget: $%{y:,.0f}<extra></extra>",
    ))
    fig_bva.add_trace(go.Scatter(
        name="Variance $", x=months, y=variances,
        mode="lines+markers",
        line=dict(color="#f59e0b", width=2),
        marker=dict(color=var_colors, size=7, line=dict(width=1, color="white")),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>Variance: $%{y:,.0f}<extra></extra>",
    ))
    fig_bva.update_layout(
        barmode="group",
        margin=dict(l=60, r=70, t=10, b=40),
        paper_bgcolor="transparent", plot_bgcolor="transparent",
        font=_FONT, hoverlabel=_HOVER,
        legend=dict(orientation="h", x=0, y=1.08, font=dict(size=11)),
        xaxis=dict(gridcolor=GRID, zeroline=False),
        yaxis=dict(tickformat="$,.0f", gridcolor=GRID, zeroline=False),
        yaxis2=dict(
            overlaying="y", side="right",
            tickformat="$,.0f",
            zeroline=True, zerolinecolor="#94a3b8", zerolinewidth=1,
            showgrid=False, title="Variance",
        ),
    )
    charts_budget = {"monthly_rev": plotly.io.to_json(fig_bva, remove_uids=True)}

    result = (pl_bud, rev_df_bud, cost_df_bud, charts_budget)
    _BUD_CACHE[ck] = result
    return result


# ── System prompt for BvA Claude analysis ────────────────────────────────────

_BVA_SYSTEM = (
    "You are a CFO analysing budget variances.\n\n"
    "You will be given budget vs actual figures for a business.\n"
    "Identify the top 3 most significant variances (by absolute dollar amount), "
    "explain the likely cause of each, and recommend a specific corrective action.\n\n"
    "Return a JSON array of exactly 3 objects with this structure:\n"
    "[{\"item\": \"Revenue\", \"variance_abs\": -45000, \"variance_pct\": -4.2, "
    "\"is_favourable\": false, \"explanation\": \"two sentences\", "
    "\"recommendation\": \"one actionable sentence\"}]\n\n"
    "Rules:\n"
    "- Be specific and reference actual numbers from the data\n"
    "- explanation: 2 sentences max — what happened and likely cause\n"
    "- recommendation: 1 sentence — specific corrective action\n"
    "- is_favourable: true if variance benefits the business (revenue above budget OR costs below budget)\n"
    "- Return ONLY valid JSON array — no markdown, no fences, no text outside the array"
)


# ── Routes ────────────────────────────────────────────────────────────────────

@main.route("/")
def index():
    return render_template("upload.html", slots=FILE_SLOTS, slots_py=FILE_SLOTS_PY, slots_bud=FILE_SLOTS_BUD)


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
        return render_template("upload.html", slots=FILE_SLOTS,
                               slots_py=FILE_SLOTS_PY, slots_bud=FILE_SLOTS_BUD, errors=errors)

    # ── Step 2b: Save prior-year files if provided (all optional) ────────────
    prior_year_dir = session_dir / "prior_year"
    for key, filename, label, _ncols, _hint in FILE_SLOTS_PY:
        f = request.files.get(key)
        if f and f.filename and f.filename.lower().endswith(".xlsx"):
            prior_year_dir.mkdir(exist_ok=True)
            f.save(prior_year_dir / filename)

    # ── Step 2c: Save budget files if provided (all optional) ────────────
    budget_dir = session_dir / "budget"
    for key, filename, label, _ncols, _hint in FILE_SLOTS_BUD:
        f = request.files.get(key)
        if f and f.filename and f.filename.lower().endswith(".xlsx"):
            budget_dir.mkdir(exist_ok=True)
            f.save(budget_dir / filename)

    # ── Step 3: Go straight to dashboard (direct Excel loaders, no AI) ──────────
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
                               slots_py=FILE_SLOTS_PY, slots_bud=FILE_SLOTS_BUD,
                               errors=[f"Could not generate dashboard: {e}"])

    # ── Prior-year comparison ─────────────────────────────────────────────────
    has_prior_year = False
    kpis_py        = {}
    charts_compare = {}
    cost_legend_py = []
    kpi_compare    = {}

    prior_year_dir = (data_dir / "prior_year") if data_dir else None
    if session_id == "demo" or (prior_year_dir and prior_year_dir.exists()):
        try:
            kpis_py, charts_compare, cost_legend_py = _build_prior_year_data(
                data_dir, prior_year_dir
            )
            has_prior_year = True

            # Compute YoY deltas
            _hib = {
                'total_revenue': True, 'gross_profit': True, 'gross_margin': True,
                'ebitda': True, 'ebitda_margin': True, 'net_profit': True,
                'net_margin': True, 'current_ratio': True,
                'debt_to_equity': False,   # lower D/E is better
                'total_assets': True, 'total_equity': True, 'total_liabilities': False,
            }
            for key, hib in _hib.items():
                cy_val = kpis.get(key, 0) or 0
                py_val = kpis_py.get(key, 0) or 0
                if py_val:
                    dp = round((cy_val - py_val) / abs(py_val) * 100, 1)
                else:
                    dp = None
                is_up    = cy_val > py_val
                is_good  = (is_up and hib) or (not is_up and not hib)
                both_neg = cy_val < 0 and py_val < 0
                kpi_compare[key] = {
                    "py":        py_val,
                    "delta_pct": dp,
                    "is_up":     is_up,
                    "is_good":   is_good,
                    "both_neg":  both_neg,
                }
        except Exception:
            has_prior_year = False   # silently degrade

    # ── Budget vs Actuals ─────────────────────────────────────────────────────
    has_budget      = False
    budget_cards    = {}
    budget_var_rows = []
    charts_budget   = {}

    budget_dir = (data_dir / "budget") if data_dir else None
    if session_id == "demo" or (budget_dir and budget_dir.exists()):
        try:
            pl_bud, rev_df_bud, cost_df_bud, charts_budget = _build_budget_data(
                data_dir, budget_dir
            )
            has_budget = True

            # ── Variance summary cards ────────────────────────────────────────
            def _var(actual, budget_val):
                budget_val = float(budget_val) if budget_val else 0.0
                var_abs = actual - budget_val
                var_pct = round(var_abs / abs(budget_val) * 100, 1) if budget_val else 0.0
                return {"actual": actual, "budget": budget_val,
                        "var_abs": var_abs, "var_pct": var_pct}

            act_rev = kpis.get('total_revenue', 0) or 0
            act_gp  = kpis.get('gross_profit', 0) or 0
            act_np  = kpis.get('net_profit', 0) or 0
            # Total costs: revenue minus gross profit (approx)
            act_tc  = act_rev - act_gp

            bud_rev = float(pl_bud.get('Total Revenue') or 0)
            bud_gp  = float(pl_bud.get('Gross Profit') or 0)
            bud_np  = float(pl_bud.get('Net Profit After Tax') or pl_bud.get('Net Profit') or 0)
            bud_tc  = bud_rev - bud_gp if bud_gp else 0.0

            rv = _var(act_rev, bud_rev); rv['favourable'] = act_rev >= bud_rev
            gv = _var(act_gp,  bud_gp);  gv['favourable'] = act_gp  >= bud_gp
            tc = _var(act_tc,  bud_tc);  tc['favourable'] = act_tc  <= bud_tc  # lower costs = good
            nv = _var(act_np,  bud_np);  nv['favourable'] = act_np  >= bud_np
            budget_cards = {'revenue': rv, 'gross_profit': gv, 'total_costs': tc, 'net_profit': nv}

            # ── Variance table (all shared P&L line items) ────────────────────
            if _has_extracted_json(data_dir):
                pl_actual = load_pl_json(data_dir)
            else:
                pl_actual = load_pl(data_dir)

            _COST_KW = ('cost', 'expense', 'payroll', 'wage', 'salary', 'staff',
                        'rent', 'lease', 'marketing', 'advertis', 'depreci',
                        'amort', 'interest', 'admin', 'overhead', 'labour', 'labor')

            rows = []
            for item, act_val in pl_actual.items():
                if item not in pl_bud:
                    continue
                try:
                    av = float(act_val or 0)
                    bv = float(pl_bud[item] or 0)
                except (TypeError, ValueError):
                    continue
                if av == 0 and bv == 0:
                    continue
                var_abs = av - bv
                var_pct = round(var_abs / abs(bv) * 100, 1) if bv else 0.0
                is_cost = any(k in item.lower() for k in _COST_KW)
                # Favourable: revenue/profit above budget OR cost below budget
                favourable = (not is_cost and av >= bv) or (is_cost and av <= bv)
                if is_cost:
                    status = "On Track" if av <= bv else "Over Budget"
                else:
                    status = "On Track" if av >= bv else "Below Target"
                rows.append({
                    'item': item,
                    'budget': bv,
                    'actual': av,
                    'var_abs': var_abs,
                    'var_pct': var_pct,
                    'favourable': favourable,
                    'status': status,
                    'sort_key': abs(var_abs),
                })
            rows.sort(key=lambda r: r['sort_key'], reverse=True)
            budget_var_rows = rows[:15]   # top 15 by magnitude

        except Exception:
            has_budget = False   # silently degrade

    return render_template(
        "dashboard.html",
        kpis=kpis, charts=charts, cost_legend=cost_legend,
        session_id=session_id,
        has_prior_year=has_prior_year,
        kpis_py=kpis_py,
        charts_compare=charts_compare,
        cost_legend_py=cost_legend_py,
        kpi_compare=kpi_compare,
        has_budget=has_budget,
        budget_cards=budget_cards,
        budget_var_rows=budget_var_rows,
        charts_budget=charts_budget,
    )


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

def _build_financial_context(data_dir, kpis, cost_legend, kpis_py=None) -> str:
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

    # ── Prior-year KPI comparison ─────────────────────────────────────────────
    if kpis_py:
        lines += [
            "=== PRIOR YEAR KPIs (FY2024) ===",
            f"  Total Revenue:          ${kpis_py.get('total_revenue',    0):>14,.0f}",
            f"  Gross Profit:           ${kpis_py.get('gross_profit',     0):>14,.0f}  ({kpis_py.get('gross_margin', 0):.1f}% margin)",
            f"  EBITDA:                 ${kpis_py.get('ebitda',           0):>14,.0f}  ({kpis_py.get('ebitda_margin',0):.1f}% margin)",
            f"  Net Profit After Tax:   ${kpis_py.get('net_profit',       0):>14,.0f}  ({kpis_py.get('net_margin',   0):.1f}% margin)",
            f"  Total Assets:           ${kpis_py.get('total_assets',     0):>14,.0f}",
            f"  Current Ratio:          {kpis_py.get('current_ratio',     0):>13.2f}x",
            "",
        ]
        # YoY changes
        def _yoy(cy, py): return f"{((cy-py)/abs(py)*100):+.1f}%" if py else "N/A"
        lines += [
            "=== YEAR-ON-YEAR CHANGES ===",
            f"  Revenue:       {_yoy(kpis.get('total_revenue',0), kpis_py.get('total_revenue',0))}",
            f"  Gross Margin:  {kpis.get('gross_margin',0):.1f}% vs {kpis_py.get('gross_margin',0):.1f}% (FY2024)",
            f"  EBITDA Margin: {kpis.get('ebitda_margin',0):.1f}% vs {kpis_py.get('ebitda_margin',0):.1f}% (FY2024)",
            f"  Net Margin:    {kpis.get('net_margin',0):.1f}% vs {kpis_py.get('net_margin',0):.1f}% (FY2024)",
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

    try:
        kpis_py_ctx, _, _ = _build_prior_year_data(data_dir,
            (data_dir / "prior_year") if data_dir else None)
    except Exception:
        kpis_py_ctx = None
    context = _build_financial_context(data_dir, kpis, cost_legend, kpis_py=kpis_py_ctx)

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
_FORECAST_SYSTEM = (
    "You are a financial analyst generating a 6-month revenue forecast.\n\n"
    "You will receive 12 months of historical monthly revenue data and a statistical baseline.\n"
    "Return a JSON object with this EXACT structure (no markdown, no code fences, no extra text):\n"
    "{\"forecast\":["
    "{\"month\":\"Jan 26\",\"low_estimate\":120000,\"base_estimate\":145000,"
    "\"high_estimate\":170000,\"reasoning\":\"one plain-English sentence\"},"
    "... 5 more months ...]}\n\n"
    "Rules:\n"
    "- Consider seasonality, growth trends, and anomalies in the data\n"
    "- Base estimate = your best single-point forecast; low/high = realistic bounds (not extreme)\n"
    "- Reasoning: one concise sentence per month referencing specific patterns you observe\n"
    "- Months must be labelled exactly: Jan 26, Feb 26, Mar 26, Apr 26, May 26, Jun 26\n"
    "- Return ONLY valid JSON — nothing outside the JSON object"
)


@main.route("/api/forecast/<session_id>")
def api_forecast(session_id):
    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return jsonify({"error": "Session not found"}), 404
        data_dir = session_dir

    # ── Load & reconcile revenue data ─────────────────────────────────────────
    try:
        if _has_extracted_json(data_dir):
            rev_df = load_revenue_json(data_dir)
        else:
            rev_df = load_revenue(data_dir)
        # Reconcile monthly totals to P&L annual total (same as dashboard)
        try:
            kpis, _, _ = _build_dashboard_data(data_dir)
            pl_rev  = kpis.get("total_revenue", 0) or 0
            raw_tot = rev_df["Total"].sum()
            if raw_tot > 0 and pl_rev > 0:
                scale = pl_rev / raw_tot
                for col in [c for c in rev_df.columns if c != "Month"]:
                    rev_df[col] = (rev_df[col] * scale).round(0).astype(int)
        except Exception:
            pass
    except Exception as exc:
        return jsonify({"error": f"Could not load revenue data: {exc}"}), 500

    # ── Statistical baseline ──────────────────────────────────────────────────
    stat_fc, confidence, avg_growth_pct = _statistical_forecast(rev_df)
    fc_months = [dict(m) for m in stat_fc]   # working copy

    # ── Check prior-year availability ─────────────────────────────────────────
    prior_year_dir = (data_dir / "prior_year") if data_dir else None
    has_prior = session_id == "demo" or bool(prior_year_dir and prior_year_dir.exists())

    # ── Claude AI enhancement (best-effort, falls back to statistical) ────────
    api_key     = os.environ.get("ANTHROPIC_API_KEY", "")
    claude_used = False
    if api_key and stat_fc:
        try:
            import anthropic as _ant
            import json as _json
            client   = _ant.Anthropic(api_key=api_key)
            from .insights import _best_model
            model_id, _ = _best_model(client)
            if model_id:
                months_list = rev_df["Month"].tolist()
                totals_list = rev_df["Total"].tolist()
                tbl = "\n".join(f"  {m}: ${t:,.0f}"
                                for m, t in zip(months_list, totals_list))
                stat_summary = "\n".join(
                    f"  {s['month_label']}: base=${s['base']:,.0f}"
                    f"  low=${s['low']:,.0f}  high=${s['high']:,.0f}"
                    for s in stat_fc
                )
                prompt = (
                    f"Historical monthly revenue (FY2025):\n{tbl}\n\n"
                    f"Statistical baseline for next 6 months:\n{stat_summary}\n\n"
                    "Generate your AI-enhanced forecast for Jan 26 – Jun 26."
                )
                msg = client.messages.create(
                    model=model_id,
                    max_tokens=900,
                    system=_FORECAST_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = msg.content[0].text.strip()
                # Strip any accidental markdown fences
                if "```" in raw:
                    start = raw.find("{")
                    end   = raw.rfind("}") + 1
                    raw   = raw[start:end] if start != -1 else raw
                parsed = _json.loads(raw)
                cl_fc  = parsed.get("forecast", [])
                if len(cl_fc) == 6:
                    for i, (sf, cf) in enumerate(zip(stat_fc, cl_fc)):
                        fc_months[i] = {
                            "month_label": sf["month_label"],
                            "month_num":   sf["month_num"],
                            "base": int(cf.get("base_estimate", sf["base"])),
                            "low":  int(cf.get("low_estimate",  sf["low"])),
                            "high": int(cf.get("high_estimate", sf["high"])),
                            "reasoning": cf.get("reasoning", ""),
                        }
                    claude_used = True
        except Exception:
            pass   # silent fallback to statistical

    # ── Summary ───────────────────────────────────────────────────────────────
    actuals  = [int(v) for v in rev_df["Total"].tolist()]
    months_l = rev_df["Month"].tolist()
    h1_2026  = sum(f["base"] for f in fc_months)
    h1_2025  = sum(actuals[:6]) if len(actuals) >= 6 else sum(actuals)
    growth_pct = round((h1_2026 - h1_2025) / h1_2025 * 100, 1) if h1_2025 else 0.0

    return jsonify({
        "actual_months":   months_l,
        "actual_totals":   actuals,
        "forecast":        fc_months,
        "summary": {
            "h1_2026":    round(h1_2026),
            "h1_2025":    round(h1_2025),
            "growth_pct": growth_pct,
            "confidence": confidence,
        },
        "avg_growth_rate": avg_growth_pct,
        "has_prior_year":  has_prior,
        "claude_used":     claude_used,
        "error":           None,
    })


_ANOMALY_SYSTEM = (
    "You are a financial analyst detecting anomalies and red flags in company financials.\n\n"
    "Analyse the provided data and return a JSON array of anomaly objects. Each object must have:\n"
    "- \"severity\": exactly one of \"High\", \"Medium\", or \"Low\"\n"
    "- \"description\": one concise sentence with specific dollar amounts and context, "
    "e.g. 'December COGS of $73K is 2.1× the monthly average of $35K — review recommended'\n\n"
    "Check for these anomaly types:\n"
    "1. COST SPIKES: any month where a cost category exceeds 150% of its 12-month average "
    "(High if >200%, Medium if 150–200%)\n"
    "2. REVENUE DROPS: any month where total revenue falls more than 15% from the prior month "
    "(High if >30% drop, Medium if 15–30%)\n"
    "3. KPI HEALTH: current ratio <1.0 = High, 1.0–1.5 = Medium; gross margin <20% = High, "
    "20–40% = Medium; debt/equity >2.0 = High, 1.5–2.0 = Medium; negative net margin = High\n"
    "4. BALANCE SHEET: negative equity = High; total liabilities >2× total assets = High; "
    "current liabilities >80% of current assets = Medium\n\n"
    "Rules:\n"
    "- Return ONLY a valid JSON array — no markdown, no explanation, no code fences\n"
    "- If no anomalies exist, return an empty array: []\n"
    "- Keep descriptions concise and always include specific numbers\n"
    "- Sort by severity: High first, then Medium, then Low\n"
    "- Maximum 8 anomalies total\n"
    "- Do not flag the same issue twice"
)


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

    try:
        kpis_py_ctx, _, _ = _build_prior_year_data(data_dir,
            (data_dir / "prior_year") if data_dir else None)
    except Exception:
        kpis_py_ctx = None
    context = _build_financial_context(data_dir, kpis, cost_legend, kpis_py=kpis_py_ctx)

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
            max_tokens=1500,
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


@main.route("/api/anomalies/<session_id>")
def api_anomalies(session_id):
    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return jsonify({"anomalies": [], "error": "Session not found"}), 404
        data_dir = session_dir

    try:
        kpis, _, cost_legend = _build_dashboard_data(data_dir)
    except Exception as exc:
        return jsonify({"anomalies": [], "error": f"Could not load data: {exc}"}), 500

    context = _build_financial_context(data_dir, kpis, cost_legend)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"anomalies": [], "error": "ANTHROPIC_API_KEY not configured"}), 503

    try:
        import anthropic
    except ImportError:
        return jsonify({"anomalies": [], "error": "anthropic package not installed"}), 503

    client = anthropic.Anthropic(api_key=api_key)

    try:
        from .insights import _best_model
        model_id, err = _best_model(client)
    except Exception as exc:
        return jsonify({"anomalies": [], "error": str(exc)}), 500

    if not model_id:
        return jsonify({"anomalies": [], "error": err}), 503

    try:
        import json as _json
        message = client.messages.create(
            model=model_id,
            max_tokens=900,
            system=_ANOMALY_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Financial data:\n\n{context}\n\n"
                    "Detect all anomalies and return the JSON array now."
                ),
            }],
        )
        raw = message.content[0].text.strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw[raw.index("["):]
        if raw.endswith("```"):
            raw = raw[: raw.rindex("]") + 1]
        anomalies = _json.loads(raw)
        if not isinstance(anomalies, list):
            anomalies = []
    except Exception as exc:
        return jsonify({"anomalies": [], "error": f"Claude error: {exc}"}), 500

    return jsonify({"anomalies": anomalies, "error": None})


@main.route("/api/bva-analysis/<session_id>")
def api_bva_analysis(session_id):
    if session_id == "demo":
        data_dir = None
    else:
        session_dir = UPLOAD_DIR / session_id
        if not session_dir.exists():
            return jsonify({"analysis": [], "error": "Session not found"}), 404
        data_dir = session_dir

    try:
        kpis, _, _ = _build_dashboard_data(data_dir)
    except Exception as exc:
        return jsonify({"analysis": [], "error": f"Could not load data: {exc}"}), 500

    budget_dir = (data_dir / "budget") if data_dir else None
    try:
        pl_bud, rev_df_bud, cost_df_bud, _ = _build_budget_data(data_dir, budget_dir)
    except Exception as exc:
        return jsonify({"analysis": [], "error": f"Budget data error: {exc}"}), 500

    # Build variance summary for Claude
    actual_rev = kpis.get('total_revenue', 0) or 0
    budget_rev = float(pl_bud.get('Total Revenue') or 0)
    actual_gp  = kpis.get('gross_profit', 0) or 0
    budget_gp  = float(pl_bud.get('Gross Profit') or 0)
    actual_np  = kpis.get('net_profit', 0) or 0
    budget_np  = float(pl_bud.get('Net Profit After Tax') or pl_bud.get('Net Profit') or 0)

    context = (
        f"Budget vs Actual — FY2025\n\n"
        f"Revenue:      Actual ${actual_rev:,.0f}  |  Budget ${budget_rev:,.0f}  |  Variance ${actual_rev - budget_rev:,.0f}\n"
        f"Gross Profit: Actual ${actual_gp:,.0f}  |  Budget ${budget_gp:,.0f}  |  Variance ${actual_gp - budget_gp:,.0f}\n"
        f"Net Profit:   Actual ${actual_np:,.0f}  |  Budget ${budget_np:,.0f}  |  Variance ${actual_np - budget_np:,.0f}\n\n"
    )
    # Add monthly revenue variance
    try:
        if _has_extracted_json(data_dir):
            rev_df_cy = load_revenue_json(data_dir)
        else:
            rev_df_cy = load_revenue(data_dir)
        months = rev_df_cy["Month"].tolist()
        act_t  = rev_df_cy["Total"].tolist()
        bud_t  = rev_df_bud["Total"].tolist()
        context += "Monthly Revenue Variance:\n"
        for m, a, b in zip(months, act_t, bud_t):
            context += f"  {m}: Actual ${float(a):,.0f}  Budget ${float(b):,.0f}  Var ${float(a)-float(b):,.0f}\n"
    except Exception:
        pass

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"analysis": [], "error": "ANTHROPIC_API_KEY not configured"}), 503

    try:
        import anthropic as _ant, json as _json
        client = _ant.Anthropic(api_key=api_key)
        from .insights import _best_model
        model_id, err = _best_model(client)
    except Exception as exc:
        return jsonify({"analysis": [], "error": str(exc)}), 500

    if not model_id:
        return jsonify({"analysis": [], "error": err}), 503

    try:
        msg = client.messages.create(
            model=model_id,
            max_tokens=800,
            system=_BVA_SYSTEM,
            messages=[{"role": "user", "content": context + "\nAnalyse these budget variances now."}],
        )
        raw = msg.content[0].text.strip()
        if "```" in raw:
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            raw   = raw[start:end] if start != -1 else raw
        analysis = _json.loads(raw)
        if not isinstance(analysis, list):
            analysis = []
    except Exception as exc:
        return jsonify({"analysis": [], "error": f"Claude error: {exc}"}), 500

    return jsonify({"analysis": analysis, "error": None})
