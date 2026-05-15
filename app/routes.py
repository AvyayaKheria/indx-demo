import json
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from flask import Blueprint, render_template

from .loader import load_balance_sheet, load_costs, load_pl, load_revenue, load_trial_balance

main = Blueprint("main", __name__)

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
    """Base layout for all standard (non-subplot) charts."""
    yaxis = dict(_YAXIS)
    if y_title:
        yaxis["title"] = dict(text=y_title, font=dict(size=11, color=SLATE), standoff=12)
    m = margin or dict(l=80, r=24, t=48, b=64)
    return dict(
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=_FONT,
        hoverlabel=_HOVER,
        legend=_LEGEND,
        xaxis=dict(_XAXIS),
        yaxis=yaxis,
        margin=m,
        title=dict(
            text=title,
            font=dict(size=13, color="#1e293b"),
            x=0, xanchor="left", pad=dict(l=8, t=4),
        ),
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

    # ── Reconcile monthly revenue to P&L total ────────────────────────────────
    pl_revenue = pl.get("Total Revenue", 0)
    raw_total  = rev_df["Total"].sum()
    if raw_total > 0 and pl_revenue > 0:
        scale = pl_revenue / raw_total
        for col in ["Dine_In", "Delivery", "Catering", "Total"]:
            rev_df[col] = (rev_df[col] * scale).round(0).astype(int)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    total_revenue       = pl.get("Total Revenue", 0)
    gross_profit        = pl.get("Gross Profit", 0)
    ebitda              = pl.get("EBITDA", 0)
    net_profit          = pl.get("Net Profit After Tax", 0)
    total_assets        = bs.get("TOTAL ASSETS", 0)
    total_equity        = bs.get("Total Equity", 0)
    current_assets      = bs.get("Total Current Assets", 0)
    current_liabilities = bs.get("Total Current Liabilities", 0)
    non_current_liab    = bs.get("Total Non-Current Liabilities", 0)
    total_liabilities   = current_liabilities + non_current_liab
    non_current_assets  = bs.get("Total Non-Current Assets", 0)

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

    # ─────────────────────────────────────────────────────────────────────────
    # Chart 1 — Monthly Revenue by Channel (stacked bar + total labels)
    # ─────────────────────────────────────────────────────────────────────────
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

    # Monthly total labels above each stacked bar
    for month, total in zip(months, rev_df["Total"].tolist()):
        fig_rev.add_annotation(
            x=month, y=total,
            text=f"<b>${total/1e3:.0f}K</b>",
            showarrow=False,
            yanchor="bottom", yshift=5,
            font=dict(size=9, color=SLATE),
        )

    fig_rev.update_layout(
        **_layout("Monthly Revenue by Channel", y_title="Monthly Revenue ($)"),
        barmode="stack",
        bargap=0.35,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Chart 2 — Revenue vs Costs vs Operating Profit (line)
    # ─────────────────────────────────────────────────────────────────────────
    fig_rvc = go.Figure()
    profit_monthly = [r - c for r, c in zip(rev_df["Total"].tolist(), cost_df["Total"].tolist())]

    for y_vals, color, name, dash in [
        (rev_df["Total"].tolist(),  NAVY,  "Revenue",          "solid"),
        (cost_df["Total"].tolist(), RED,   "Total Costs",      "solid"),
        (profit_monthly,            TEAL,  "Operating Profit", "dot"),
    ]:
        fig_rvc.add_trace(go.Scatter(
            x=months, y=y_vals, name=name, mode="lines+markers",
            line=dict(color=color, width=2.5, dash=dash),
            marker=dict(size=7, color=color, line=dict(color="white", width=1.5)),
            hovertemplate=f"<b>%{{x}}</b><br>{name}: $%{{y:,.0f}}<extra></extra>",
        ))

    # Min/max annotations on Revenue line
    rev_vals = rev_df["Total"].tolist()
    max_v, min_v = max(rev_vals), min(rev_vals)
    max_m, min_m = months[rev_vals.index(max_v)], months[rev_vals.index(min_v)]

    for month, val, label, color, ay in [
        (max_m, max_v, f"Peak  ${max_v/1e3:.0f}K", GREEN, -36),
        (min_m, min_v, f"Low  ${min_v/1e3:.0f}K",  RED,    36),
    ]:
        fig_rvc.add_annotation(
            x=month, y=val, text=label,
            showarrow=True, arrowhead=2, arrowsize=0.8, arrowwidth=1.5,
            arrowcolor=color, ax=0, ay=ay,
            font=dict(size=10, color=color),
            bgcolor="white", borderpad=3,
            bordercolor=color, borderwidth=1,
        )

    fig_rvc.update_layout(**_layout("Revenue vs Costs vs Operating Profit", y_title="Amount ($)"))

    # ─────────────────────────────────────────────────────────────────────────
    # Chart 3 — Cost Mix donut
    # ─────────────────────────────────────────────────────────────────────────
    cost_labels = ["F&B / COGS", "Payroll", "Rent & Utilities", "Marketing"]
    cost_vals   = [int(cost_df[c].sum()) for c in ["COGS", "Payroll", "Rent", "Marketing"]]
    total_costs = sum(cost_vals)

    fig_cost = go.Figure(go.Pie(
        labels=cost_labels,
        values=cost_vals,
        hole=0.54,
        marker=dict(colors=[NAVY, TEAL, TEAL2, TEAL3], line=dict(color="white", width=2.5)),
        texttemplate="<b>%{label}</b><br>$%{value:,.0f}<br>(%{percent})",
        textfont=dict(size=10),
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f} · %{percent}<extra></extra>",
        sort=False,
        automargin=True,
        direction="clockwise",
    ))

    # Total cost in donut centre
    fig_cost.add_annotation(
        text=f"<b>${total_costs/1e6:.2f}M</b><br><span style='font-size:10px'>Total Costs</span>",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=14, color=NAVY),
        align="center",
    )

    fig_cost.update_layout(
        paper_bgcolor="white", plot_bgcolor="white",
        font=_FONT, hoverlabel=_HOVER,
        title=dict(text="Cost Mix — FY2025", font=dict(size=13, color="#1e293b"),
                   x=0, xanchor="left", pad=dict(l=8, t=4)),
        margin=dict(l=24, r=24, t=48, b=72),
        legend=dict(orientation="h", y=-0.1, font=dict(size=11, color=SLATE),
                    bgcolor="rgba(0,0,0,0)"),
        showlegend=True,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Chart 4 — P&L Waterfall
    # ─────────────────────────────────────────────────────────────────────────
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
        connector=dict(line=dict(color=BORDER, width=1.5, dash="dot")),
        increasing=dict(marker=dict(color=TEAL,  line=dict(width=0))),
        decreasing=dict(marker=dict(color=RED,   line=dict(width=0))),
        totals=dict(marker=dict(color=NAVY,      line=dict(width=0))),
        textposition="outside",
        textfont=dict(size=10, color="#334155"),
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
    fig_wf.update_layout(**_layout("P&L Waterfall — FY2025", margin=dict(l=80, r=24, t=48, b=80)))
    fig_wf.update_xaxes(tickangle=-45)

    # ─────────────────────────────────────────────────────────────────────────
    # Chart 5 — Balance Sheet Composition
    # ─────────────────────────────────────────────────────────────────────────
    fig_bs = go.Figure()
    bs_items = [
        ("Current Assets",          "Assets",               current_assets,     TEAL2),
        ("Non-Current Assets",      "Assets",               non_current_assets, NAVY),
        ("Current Liabilities",     "Liabilities & Equity", current_liabilities,  RED),
        ("Non-Current Liabilities", "Liabilities & Equity", non_current_liab,     "#b91c1c"),
        ("Equity",                  "Liabilities & Equity", total_equity,          GREEN),
    ]
    for name, cat, val, color in bs_items:
        fig_bs.add_trace(go.Bar(
            name=name, x=[cat], y=[val],
            marker_color=color, marker_line_width=0,
            text=[f"${val:,.0f}"],
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=10, color="white"),
            constraintext="inside",
            hovertemplate=f"<b>{name}</b><br>${val:,.0f}<extra></extra>",
        ))
    fig_bs.update_layout(
        **_layout("Balance Sheet Composition", y_title="Amount ($)"),
        barmode="stack", bargap=0.5,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Chart 6 — Trial Balance (two horizontal bar subplots)
    # ─────────────────────────────────────────────────────────────────────────
    debit_df  = tb[tb["Debit"].notna()  & (tb["Debit"]  > 0)].sort_values("Debit",  ascending=True)
    credit_df = tb[tb["Credit"].notna() & (tb["Credit"] > 0)].sort_values("Credit", ascending=True)

    total_debits  = float(debit_df["Debit"].sum())
    total_credits = float(credit_df["Credit"].sum())
    is_balanced   = abs(total_debits - total_credits) < 1

    n_d, n_c = len(debit_df), len(credit_df)

    fig_tb = make_subplots(
        rows=2, cols=1,
        row_heights=[n_d / (n_d + n_c), n_c / (n_d + n_c)],
        vertical_spacing=0.10,
        subplot_titles=["Debit Accounts", "Credit Accounts"],
    )

    # Debit bars
    fig_tb.add_trace(go.Bar(
        orientation="h",
        x=debit_df["Debit"].tolist(),
        y=debit_df["Account"].tolist(),
        text=[f"${v:,.0f}" for v in debit_df["Debit"].tolist()],
        textposition="outside",
        textfont=dict(size=10, color=NAVY),
        marker_color=NAVY, marker_line_width=0,
        name=f"Total Debits: ${total_debits:,.0f}",
        hovertemplate="<b>%{y}</b><br>$%{x:,.0f}<extra></extra>",
        cliponaxis=False,
    ), row=1, col=1)

    # Credit bars
    fig_tb.add_trace(go.Bar(
        orientation="h",
        x=credit_df["Credit"].tolist(),
        y=credit_df["Account"].tolist(),
        text=[f"${v:,.0f}" for v in credit_df["Credit"].tolist()],
        textposition="outside",
        textfont=dict(size=10, color=TEAL),
        marker_color=TEAL, marker_line_width=0,
        name=f"Total Credits: ${total_credits:,.0f}",
        hovertemplate="<b>%{y}</b><br>$%{x:,.0f}<extra></extra>",
        cliponaxis=False,
    ), row=2, col=1)

    # Style all x-axes (horizontal value axes)
    fig_tb.update_xaxes(
        showgrid=True, gridcolor=GRID, gridwidth=1,
        linecolor=BORDER, linewidth=1,
        tickfont=dict(size=10, color=SLATE),
        tickprefix="$", tickformat=",.0f",
        zeroline=False,
    )
    # Style all y-axes (account name axes)
    fig_tb.update_yaxes(
        showgrid=False,
        linecolor=BORDER, linewidth=1,
        tickfont=dict(size=11, color="#1e293b"),
        automargin=True,
    )

    # Subplot title font
    for ann in fig_tb.layout.annotations:
        ann.update(font=dict(size=11, color=SLATE, family="Inter, system-ui, sans-serif"),
                   x=0, xanchor="left")

    # Balance footnote
    if is_balanced:
        fn_text  = f"✓ Trial balance is balanced — Total Debits = Total Credits = ${total_debits:,.0f}"
        fn_color = "#166534"
        fn_bg    = "#f0fdf4"
        fn_border= "#bbf7d0"
    else:
        diff = abs(total_debits - total_credits)
        fn_text  = (f"⚠ Trial balance does not balance — "
                    f"Debits: ${total_debits:,.0f}  |  Credits: ${total_credits:,.0f}  |  "
                    f"Difference: ${diff:,.0f}")
        fn_color = "#991b1b"
        fn_bg    = "#fef2f2"
        fn_border= "#fecaca"

    fig_tb.add_annotation(
        text=fn_text,
        xref="paper", yref="paper",
        x=0, y=-0.04,
        showarrow=False,
        font=dict(size=10, color=fn_color, family="Inter, system-ui, sans-serif"),
        bgcolor=fn_bg,
        borderpad=5,
        bordercolor=fn_border,
        borderwidth=1,
        xanchor="left",
        align="left",
    )

    fig_tb.update_layout(
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=_FONT,
        hoverlabel=_HOVER,
        title=dict(
            text="Trial Balance FY2025 — Debit & Credit Accounts",
            font=dict(size=13, color="#1e293b"),
            x=0, xanchor="left", pad=dict(l=8, t=4),
        ),
        showlegend=True,
        legend=dict(
            orientation="h", x=0, y=-0.08,
            font=dict(size=11, color=SLATE),
            bgcolor="rgba(0,0,0,0)", borderwidth=0,
        ),
        margin=dict(l=210, r=90, t=52, b=90),
        height=580,
        bargap=0.25,
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
