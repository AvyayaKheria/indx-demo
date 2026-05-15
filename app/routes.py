import json
import plotly
import plotly.graph_objects as go
from flask import Blueprint, render_template

from .loader import load_balance_sheet, load_costs, load_pl, load_revenue, load_trial_balance

main = Blueprint("main", __name__)

NAVY = "#1a3a5c"
BLUE = "#2e6da4"
SKY = "#5ba3d9"
LIGHT = "#a8d1f0"
RED = "#e05c5c"
GREEN = "#27ae60"
BG = "#f8fafc"
FONT = dict(family="Inter, system-ui, sans-serif", size=13)

LAYOUT_BASE = dict(
    paper_bgcolor="white",
    plot_bgcolor=BG,
    font=FONT,
    margin=dict(l=48, r=24, t=48, b=56),
    legend=dict(orientation="h", y=-0.22, font=dict(size=12)),
)


def _json(fig: go.Figure) -> str:
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


@main.route("/")
def dashboard():
    rev_df = load_revenue()
    cost_df = load_costs()
    pl = load_pl()
    bs = load_balance_sheet()
    tb = load_trial_balance()

    # ── KPIs ────────────────────────────────────────────────────────────────
    total_revenue = pl.get("Total Revenue", 0)
    gross_profit = pl.get("Gross Profit", 0)
    ebitda = pl.get("EBITDA", 0)
    net_profit = pl.get("Net Profit After Tax", 0)
    total_assets = bs.get("TOTAL ASSETS", 0)
    total_equity = bs.get("Total Equity", 0)
    current_assets = bs.get("Total Current Assets", 0)
    current_liabilities = bs.get("Total Current Liabilities", 0)
    non_current_liabilities = bs.get("Total Non-Current Liabilities", 0)
    total_liabilities = current_liabilities + non_current_liabilities

    gross_margin = round(gross_profit / total_revenue * 100, 1) if total_revenue else 0
    net_margin = round(net_profit / total_revenue * 100, 1) if total_revenue else 0
    ebitda_margin = round(ebitda / total_revenue * 100, 1) if total_revenue else 0
    current_ratio = round(current_assets / current_liabilities, 2) if current_liabilities else 0
    debt_to_equity = round(total_liabilities / total_equity, 2) if total_equity else 0

    kpis = dict(
        total_revenue=total_revenue,
        gross_profit=gross_profit,
        gross_margin=gross_margin,
        ebitda=ebitda,
        ebitda_margin=ebitda_margin,
        net_profit=net_profit,
        net_margin=net_margin,
        current_ratio=current_ratio,
        debt_to_equity=debt_to_equity,
        total_assets=total_assets,
        total_equity=total_equity,
        total_liabilities=total_liabilities,
    )

    months = rev_df["Month"].tolist()

    # ── Chart 1: Revenue by channel — stacked bar ────────────────────────────
    fig_rev = go.Figure()
    for col, color, label in [
        ("Dine_In", NAVY, "Dine-In"),
        ("Delivery", BLUE, "Delivery"),
        ("Catering", SKY, "Catering"),
    ]:
        fig_rev.add_trace(go.Bar(name=label, x=months, y=rev_df[col].tolist(), marker_color=color))
    fig_rev.update_layout(**LAYOUT_BASE, barmode="stack", title="Monthly Revenue by Channel ($)")

    # ── Chart 2: Revenue vs Costs — dual line ────────────────────────────────
    fig_rvc = go.Figure()
    fig_rvc.add_trace(go.Scatter(
        x=months, y=rev_df["Total"].tolist(),
        name="Revenue", mode="lines+markers",
        line=dict(color=NAVY, width=2.5), marker=dict(size=6),
    ))
    fig_rvc.add_trace(go.Scatter(
        x=months, y=cost_df["Total"].tolist(),
        name="Total Costs", mode="lines+markers",
        line=dict(color=RED, width=2.5), marker=dict(size=6),
    ))
    # profit line
    profit_monthly = [r - c for r, c in zip(rev_df["Total"].tolist(), cost_df["Total"].tolist())]
    fig_rvc.add_trace(go.Scatter(
        x=months, y=profit_monthly,
        name="Operating Profit", mode="lines+markers",
        line=dict(color=GREEN, width=2, dash="dot"), marker=dict(size=5),
    ))
    fig_rvc.update_layout(**LAYOUT_BASE, title="Revenue vs Costs vs Operating Profit ($)")

    # ── Chart 3: Cost mix — donut ────────────────────────────────────────────
    cost_labels = ["COGS", "Payroll", "Rent", "Marketing"]
    cost_vals = [int(cost_df[c].sum()) for c in ["COGS", "Payroll", "Rent", "Marketing"]]
    fig_cost = go.Figure(go.Pie(
        labels=cost_labels, values=cost_vals,
        hole=0.45,
        marker_colors=[NAVY, BLUE, SKY, LIGHT],
        textinfo="label+percent",
        hovertemplate="%{label}: $%{value:,.0f}<extra></extra>",
    ))
    fig_cost.update_layout(
        paper_bgcolor="white", font=FONT,
        margin=dict(l=24, r=24, t=48, b=48),
        legend=dict(orientation="h", y=-0.15, font=dict(size=12)),
        title="Cost Breakdown FY2025",
    )

    # ── Chart 4: P&L Waterfall ───────────────────────────────────────────────
    fb_costs = pl.get("Food & Beverage Costs", 0)
    payroll = pl.get("Payroll & Staff Costs", 0)
    rent = pl.get("Rent & Utilities", 0)
    marketing = pl.get("Marketing & Advertising", 0)
    depreciation = pl.get("Depreciation", 0)
    interest = pl.get("Interest Expense", 0)

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
        connector=dict(line=dict(color="#d0d7de")),
        increasing=dict(marker=dict(color=BLUE)),
        decreasing=dict(marker=dict(color=RED)),
        totals=dict(marker=dict(color=NAVY)),
        textposition="outside",
        text=[
            f"${total_revenue/1e6:.2f}M",
            f"-${fb_costs/1e3:.0f}K",
            f"${gross_profit/1e3:.0f}K",
            f"-${payroll/1e3:.0f}K",
            f"-${rent/1e3:.0f}K",
            f"-${marketing/1e3:.0f}K",
            f"${ebitda/1e3:.0f}K",
            f"-${depreciation/1e3:.0f}K",
            f"-${interest/1e3:.0f}K",
            f"${net_profit/1e3:.0f}K",
        ],
    ))
    wf_layout = {**LAYOUT_BASE, "margin": dict(l=48, r=24, t=48, b=72)}
    fig_wf.update_layout(
        **wf_layout,
        title="P&L Waterfall FY2025 ($)",
        xaxis=dict(tickangle=-30),
    )

    # ── Chart 5: Balance Sheet composition ──────────────────────────────────
    non_current_assets = bs.get("Total Non-Current Assets", 0)
    fig_bs = go.Figure()
    for name, cat, val, color in [
        ("Current Assets", "Assets", current_assets, SKY),
        ("Non-Current Assets", "Assets", non_current_assets, NAVY),
        ("Current Liabilities", "Liabilities & Equity", current_liabilities, RED),
        ("Non-Current Liabilities", "Liabilities & Equity", non_current_liabilities, "#c0392b"),
        ("Equity", "Liabilities & Equity", total_equity, GREEN),
    ]:
        fig_bs.add_trace(go.Bar(name=name, x=[cat], y=[val], marker_color=color))
    fig_bs.update_layout(**LAYOUT_BASE, barmode="stack", title="Balance Sheet Composition ($)")

    # ── Chart 6: Trial Balance — debit vs credit by account ─────────────────
    fig_tb = go.Figure()
    fig_tb.add_trace(go.Bar(
        name="Debit", x=tb["Account"].tolist(),
        y=tb["Debit"].fillna(0).tolist(),
        marker_color=BLUE,
    ))
    fig_tb.add_trace(go.Bar(
        name="Credit", x=tb["Account"].tolist(),
        y=tb["Credit"].fillna(0).tolist(),
        marker_color=RED,
    ))
    tb_layout = {**LAYOUT_BASE, "margin": dict(l=48, r=24, t=48, b=120)}
    fig_tb.update_layout(
        **tb_layout,
        barmode="group",
        title="Trial Balance — Debit vs Credit ($)",
        xaxis=dict(tickangle=-40, tickfont=dict(size=11)),
    )

    charts = dict(
        revenue=_json(fig_rev),
        rev_cost=_json(fig_rvc),
        cost_mix=_json(fig_cost),
        waterfall=_json(fig_wf),
        balance_sheet=_json(fig_bs),
        trial_balance=_json(fig_tb),
    )

    return render_template("dashboard.html", kpis=kpis, charts=charts)
