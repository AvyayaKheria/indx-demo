import json
import plotly
import plotly.graph_objects as go
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
    """Build a clean donut figure with no slice labels and a centre annotation."""
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


@main.route("/")
def dashboard():
    rev_df  = load_revenue()
    cost_df = load_costs()
    pl      = load_pl()
    bs      = load_balance_sheet()
    load_trial_balance()         # kept for data integrity; unused in charts

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

    # ── Chart 1: Revenue by channel ───────────────────────────────────────────
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

    # ── Chart 3: Cost Mix donut (no slice labels — custom HTML legend) ─────────
    cost_labels = ["F&B / COGS", "Payroll", "Rent & Utilities", "Marketing"]
    cost_colors = [NAVY, TEAL, TEAL2, TEAL3]
    cost_vals   = [int(cost_df[c].sum()) for c in ["COGS", "Payroll", "Rent", "Marketing"]]
    total_costs = sum(cost_vals)

    fig_cost = go.Figure(go.Pie(
        labels=cost_labels, values=cost_vals,
        hole=0.54,
        marker=dict(colors=cost_colors, line=dict(color="white", width=2.5)),
        textinfo="none",
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f} · %{percent}<extra></extra>",
        sort=False, direction="clockwise",
    ))
    fig_cost.add_annotation(
        text=f"<b>${total_costs/1e6:.2f}M</b><br>Total Costs",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=14, color=NAVY), align="center",
    )
    fig_cost.update_layout(
        paper_bgcolor="white", plot_bgcolor="white",
        font=_FONT, hoverlabel=_HOVER,
        title=dict(text="Cost Mix — FY2025", font=dict(size=13, color="#1e293b"),
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
        ("Equity",                  "Liabilities & Equity", total_equity,         GREEN),
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

    return render_template("dashboard.html", kpis=kpis, charts=charts,
                           cost_legend=cost_legend)
