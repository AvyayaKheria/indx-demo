import json
import plotly
import plotly.graph_objects as go
from flask import Blueprint, render_template

from .loader import load_balance_sheet, load_costs, load_pl, load_revenue, load_trial_balance

main = Blueprint("main", __name__)

# ── Design tokens ────────────────────────────────────────────────────────────
NAVY   = "#0f2744"
TEAL   = "#0d9488"
TEAL2  = "#14b8a6"
TEAL3  = "#5eead4"
RED    = "#ef4444"
GREEN  = "#10b981"
SLATE  = "#64748b"
GRID   = "#f1f5f9"
BORDER = "#e2e8f0"

_FONT   = dict(family="Inter, system-ui, -apple-system, sans-serif", size=12, color="#475569")
_HOVER  = dict(
    bgcolor="white", bordercolor=BORDER,
    font=dict(family="Inter, system-ui, sans-serif", size=12, color="#1e293b"),
    namelength=-1,
)
_LEGEND = dict(
    orientation="h", y=-0.28,
    font=dict(size=11, color=SLATE),
    bgcolor="rgba(0,0,0,0)", borderwidth=0,
)
_XAXIS  = dict(showgrid=False, linecolor=BORDER, linewidth=1, tickfont=dict(size=11, color=SLATE))
_YAXIS  = dict(
    showgrid=True, gridcolor=GRID, gridwidth=1,
    linecolor=BORDER, linewidth=1,
    zeroline=True, zerolinecolor=BORDER, zerolinewidth=1,
    tickfont=dict(size=11, color=SLATE),
    tickprefix="$", tickformat=",.0f",
)


def _layout(title="", margin=None, extra_b=0, show_yprefix=True):
    yaxis = dict(_YAXIS)
    if not show_yprefix:
        yaxis.pop("tickprefix", None)
        yaxis.pop("tickformat", None)
    m = margin or dict(l=64, r=24, t=48, b=52 + extra_b)
    return dict(
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=_FONT,
        hoverlabel=_HOVER,
        legend=_LEGEND,
        xaxis=dict(_XAXIS),
        yaxis=yaxis,
        margin=m,
        title=dict(text=title, font=dict(size=13, color="#1e293b"), x=0, xanchor="left",
                   pad=dict(l=8, t=4)),
    )


def _json(fig: go.Figure) -> str:
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


@main.route("/")
def dashboard():
    rev_df  = load_revenue()
    cost_df = load_costs()
    pl      = load_pl()
    bs      = load_balance_sheet()
    tb      = load_trial_balance()

    # ── KPIs ─────────────────────────────────────────────────────────────────
    total_revenue        = pl.get("Total Revenue", 0)
    gross_profit         = pl.get("Gross Profit", 0)
    ebitda               = pl.get("EBITDA", 0)
    net_profit           = pl.get("Net Profit After Tax", 0)
    total_assets         = bs.get("TOTAL ASSETS", 0)
    total_equity         = bs.get("Total Equity", 0)
    current_assets       = bs.get("Total Current Assets", 0)
    current_liabilities  = bs.get("Total Current Liabilities", 0)
    non_current_liab     = bs.get("Total Non-Current Liabilities", 0)
    total_liabilities    = current_liabilities + non_current_liab
    non_current_assets   = bs.get("Total Non-Current Assets", 0)

    gross_margin   = round(gross_profit  / total_revenue * 100, 1) if total_revenue else 0
    net_margin     = round(net_profit    / total_revenue * 100, 1) if total_revenue else 0
    ebitda_margin  = round(ebitda        / total_revenue * 100, 1) if total_revenue else 0
    current_ratio  = round(current_assets / current_liabilities, 2) if current_liabilities else 0
    debt_to_equity = round(total_liabilities / total_equity, 2) if total_equity else 0

    kpis = dict(
        total_revenue=total_revenue, gross_profit=gross_profit, gross_margin=gross_margin,
        ebitda=ebitda, ebitda_margin=ebitda_margin,
        net_profit=net_profit, net_margin=net_margin,
        current_ratio=current_ratio, debt_to_equity=debt_to_equity,
        total_assets=total_assets, total_equity=total_equity, total_liabilities=total_liabilities,
    )

    months = rev_df["Month"].tolist()

    # ── Chart 1: Revenue by channel — stacked bar ────────────────────────────
    fig_rev = go.Figure()
    for col, color, label in [
        ("Dine_In",  NAVY,  "Dine-In"),
        ("Delivery", TEAL,  "Delivery"),
        ("Catering", TEAL2, "Catering"),
    ]:
        fig_rev.add_trace(go.Bar(
            name=label, x=months, y=rev_df[col].tolist(),
            marker_color=color, marker_line_width=0,
            hovertemplate=f"<b>%{{x}}</b><br>{label}: $%{{y:,.0f}}<extra></extra>",
        ))
    fig_rev.update_layout(
        **_layout("Monthly Revenue by Channel"),
        barmode="stack",
        bargap=0.35,
    )

    # ── Chart 2: Revenue vs Costs — lines ────────────────────────────────────
    fig_rvc = go.Figure()
    profit_monthly = [r - c for r, c in zip(rev_df["Total"].tolist(), cost_df["Total"].tolist())]
    for y_vals, color, name, dash in [
        (rev_df["Total"].tolist(),  NAVY,  "Revenue",          "solid"),
        (cost_df["Total"].tolist(), RED,   "Total Costs",      "solid"),
        (profit_monthly,            TEAL,  "Operating Profit", "dot"),
    ]:
        fig_rvc.add_trace(go.Scatter(
            x=months, y=y_vals, name=name, mode="lines+markers",
            line=dict(color=color, width=2, dash=dash),
            marker=dict(size=5, color=color),
            hovertemplate=f"<b>%{{x}}</b><br>{name}: $%{{y:,.0f}}<extra></extra>",
        ))
    fig_rvc.update_layout(**_layout("Revenue vs Costs vs Operating Profit"))

    # ── Chart 3: Cost mix — donut ────────────────────────────────────────────
    cost_labels = ["F&B / COGS", "Payroll", "Rent & Utilities", "Marketing"]
    cost_vals   = [int(cost_df[c].sum()) for c in ["COGS", "Payroll", "Rent", "Marketing"]]
    fig_cost = go.Figure(go.Pie(
        labels=cost_labels, values=cost_vals,
        hole=0.52,
        marker=dict(colors=[NAVY, TEAL, TEAL2, TEAL3], line=dict(color="white", width=2)),
        textinfo="label+percent",
        textfont=dict(size=11),
        hovertemplate="%{label}<br>$%{value:,.0f} (%{percent})<extra></extra>",
        sort=False,
    ))
    fig_cost.update_layout(
        paper_bgcolor="white", plot_bgcolor="white",
        font=_FONT, hoverlabel=_HOVER,
        title=dict(text="Cost Mix — FY2025", font=dict(size=13, color="#1e293b"),
                   x=0, xanchor="left", pad=dict(l=8, t=4)),
        margin=dict(l=24, r=24, t=48, b=64),
        legend=dict(orientation="h", y=-0.12, font=dict(size=11, color=SLATE),
                    bgcolor="rgba(0,0,0,0)"),
        showlegend=True,
    )

    # ── Chart 4: P&L Waterfall ───────────────────────────────────────────────
    fb_costs     = pl.get("Food & Beverage Costs", 0)
    payroll      = pl.get("Payroll & Staff Costs", 0)
    rent         = pl.get("Rent & Utilities", 0)
    marketing    = pl.get("Marketing & Advertising", 0)
    depreciation = pl.get("Depreciation", 0)
    interest     = pl.get("Interest Expense", 0)

    fig_wf = go.Figure(go.Waterfall(
        orientation="v",
        measure=["absolute", "relative", "total",
                 "relative", "relative", "relative", "total",
                 "relative", "relative", "total"],
        x=["Revenue", "F&B Costs", "Gross Profit",
           "Payroll", "Rent", "Marketing", "EBITDA",
           "Depreciation", "Interest", "Net Profit"],
        y=[total_revenue, -fb_costs, gross_profit,
           -payroll, -rent, -marketing, ebitda,
           -depreciation, -interest, net_profit],
        connector=dict(line=dict(color=BORDER, width=1, dash="dot")),
        increasing=dict(marker=dict(color=TEAL,  line=dict(width=0))),
        decreasing=dict(marker=dict(color=RED,   line=dict(width=0))),
        totals=dict(marker=dict(color=NAVY,      line=dict(width=0))),
        textposition="outside",
        textfont=dict(size=10, color=SLATE),
        text=[
            f"${total_revenue/1e6:.2f}M",
            f"−${fb_costs/1e3:.0f}K",
            f"${gross_profit/1e3:.0f}K",
            f"−${payroll/1e3:.0f}K",
            f"−${rent/1e3:.0f}K",
            f"−${marketing/1e3:.0f}K",
            f"${ebitda/1e3:.0f}K",
            f"−${depreciation/1e3:.0f}K",
            f"−${interest/1e3:.0f}K",
            f"${net_profit/1e3:.0f}K",
        ],
        hovertemplate="<b>%{x}</b><br>$%{y:,.0f}<extra></extra>",
    ))
    fig_wf.update_layout(**_layout("P&L Waterfall — FY2025", margin=dict(l=64, r=24, t=48, b=80)))
    fig_wf.update_xaxes(tickangle=-30)

    # ── Chart 5: Balance Sheet composition ───────────────────────────────────
    fig_bs = go.Figure()
    for name, cat, val, color in [
        ("Current Assets",          "Assets",              current_assets,     TEAL2),
        ("Non-Current Assets",      "Assets",              non_current_assets, NAVY),
        ("Current Liabilities",     "Liabilities & Equity", current_liabilities, RED),
        ("Non-Current Liabilities", "Liabilities & Equity", non_current_liab,    "#b91c1c"),
        ("Equity",                  "Liabilities & Equity", total_equity,        GREEN),
    ]:
        fig_bs.add_trace(go.Bar(
            name=name, x=[cat], y=[val],
            marker_color=color, marker_line_width=0,
            hovertemplate=f"<b>{name}</b><br>${val:,.0f}<extra></extra>",
        ))
    fig_bs.update_layout(
        **_layout("Balance Sheet Composition"),
        barmode="stack", bargap=0.5,
    )

    # ── Chart 6: Trial Balance ────────────────────────────────────────────────
    fig_tb = go.Figure()
    fig_tb.add_trace(go.Bar(
        name="Debit", x=tb["Account"].tolist(),
        y=tb["Debit"].fillna(0).tolist(),
        marker_color=TEAL, marker_line_width=0,
        hovertemplate="<b>%{x}</b><br>Debit: $%{y:,.0f}<extra></extra>",
    ))
    fig_tb.add_trace(go.Bar(
        name="Credit", x=tb["Account"].tolist(),
        y=tb["Credit"].fillna(0).tolist(),
        marker_color=RED, marker_line_width=0,
        hovertemplate="<b>%{x}</b><br>Credit: $%{y:,.0f}<extra></extra>",
    ))
    fig_tb.update_layout(
        **_layout("Trial Balance — Debit vs Credit", margin=dict(l=64, r=24, t=48, b=120)),
        barmode="group", bargap=0.2, bargroupgap=0.08,
    )
    fig_tb.update_xaxes(tickangle=-42, tickfont=dict(size=10, color=SLATE))

    charts = dict(
        revenue=_json(fig_rev),
        rev_cost=_json(fig_rvc),
        cost_mix=_json(fig_cost),
        waterfall=_json(fig_wf),
        balance_sheet=_json(fig_bs),
        trial_balance=_json(fig_tb),
    )

    return render_template("dashboard.html", kpis=kpis, charts=charts)
