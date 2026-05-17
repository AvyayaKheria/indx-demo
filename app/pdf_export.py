"""
PDF Export — Grain & Co. CFO Dashboard
Pure ReportLab, no system-level dependencies required.
"""
import html as _html
from io import BytesIO
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, NextPageTemplate,
    Paragraph, Spacer, Table, TableStyle, HRFlowable,
    PageBreak,
)

# ── Page dimensions ───────────────────────────────────────────────────────────
W, H = A4
MARGIN = 18 * mm
CW     = W - 2 * MARGIN    # usable content width

# ── Colour palette ─────────────────────────────────────────────────────────────
NAVY    = colors.HexColor('#0f2744')
TEAL    = colors.HexColor('#0d9488')
TEAL2   = colors.HexColor('#14b8a6')
GREEN   = colors.HexColor('#10b981')
RED     = colors.HexColor('#ef4444')
RED_DK  = colors.HexColor('#b91c1c')
AMBER   = colors.HexColor('#f59e0b')
SLATE   = colors.HexColor('#64748b')
SLATE2  = colors.HexColor('#94a3b8')
SLATE3  = colors.HexColor('#e2e8f0')
WHITE   = colors.white
DARK    = colors.HexColor('#0f172a')
BGLIGHT = colors.HexColor('#f8fafc')
BGALT   = colors.HexColor('#f1f5f9')
TEAL_BG = colors.HexColor('#f0fdfa')
GREEN_BG= colors.HexColor('#f0fdf4')
RED_BG  = colors.HexColor('#fff1f2')
BLUE_BG = colors.HexColor('#e0f2fe')


# ── Style factory ─────────────────────────────────────────────────────────────
def _ps(name, **kw):
    s = ParagraphStyle(name)
    defaults = dict(fontName='Helvetica', fontSize=10, leading=14, textColor=DARK,
                    alignment=TA_LEFT, spaceAfter=0, spaceBefore=0,
                    leftIndent=0, firstLineIndent=0)
    for k, v in {**defaults, **kw}.items():
        setattr(s, k, v)
    return s


# ── Number formatters ─────────────────────────────────────────────────────────
def _fmt(v):
    """Short format: $1.23M / $456K."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return '—'
    sign = '-' if v < 0 else ''
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}${a / 1e6:.2f}M"
    if a >= 1_000:
        return f"{sign}${a / 1e3:,.0f}K"
    return f"{sign}${a:,.0f}"


def _fmt_full(v):
    """Full format: $1,234,567."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return '—'
    sign = '-' if v < 0 else ''
    return f"{sign}${abs(v):,.0f}"


def _pct(v):
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return '—'


def _vc(v, pos_good=True):
    """Return green/red/dark based on sign and convention."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return DARK
    if pos_good:
        return GREEN if v >= 0 else RED
    return RED if v >= 0 else GREEN


def _xe(s):
    """HTML-escape a string for use in Paragraph markup."""
    return _html.escape(str(s))


# ── Canvas callbacks (page backgrounds / footers) ─────────────────────────────
def _cover_bg(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    # Teal accent strip at bottom
    canvas.setFillColor(TEAL)
    canvas.rect(0, 0, W, 3 * mm, fill=1, stroke=0)
    canvas.restoreState()


def _content_bg(canvas, doc):
    canvas.saveState()
    # Footer rule
    canvas.setStrokeColor(SLATE3)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, 14 * mm, W - MARGIN, 14 * mm)
    # Footer text
    canvas.setFillColor(SLATE2)
    canvas.setFont('Helvetica', 7.5)
    canvas.drawString(MARGIN, 10 * mm,
                      'Grain & Co.  —  CFO Dashboard Report  ·  FY2025  ·  Confidential')
    canvas.drawRightString(W - MARGIN, 10 * mm, f'Page {doc.page}')
    canvas.restoreState()


# ── Document factory ──────────────────────────────────────────────────────────
def _make_doc(buf):
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=0, rightMargin=0,
        topMargin=0, bottomMargin=0,
    )
    # Cover frame: full page, 0 margins
    cover_frame = Frame(
        MARGIN, MARGIN, CW, H - 2 * MARGIN,
        leftPadding=0, rightPadding=0,
        topPadding=0, bottomPadding=0,
        id='cover',
    )
    # Content frame: standard margins with footer clearance
    content_frame = Frame(
        MARGIN, 18 * mm,
        CW, H - MARGIN - 18 * mm,
        leftPadding=0, rightPadding=0,
        topPadding=0, bottomPadding=0,
        id='content',
    )
    doc.addPageTemplates([
        PageTemplate(id='Cover',   frames=[cover_frame],   onPage=_cover_bg),
        PageTemplate(id='Content', frames=[content_frame], onPage=_content_bg),
    ])
    return doc


# ── Shared layout helpers ─────────────────────────────────────────────────────
def _h1(text):
    return Paragraph(text, _ps('H1', fontSize=15, fontName='Helvetica-Bold',
                                textColor=NAVY, leading=20, spaceAfter=4, spaceBefore=2))


def _section_rule(label):
    return [
        Paragraph(label.upper(),
                  _ps('SecLbl', fontSize=7.5, fontName='Helvetica-Bold',
                      textColor=SLATE, leading=11, spaceAfter=4)),
        HRFlowable(width=CW, thickness=0.5, color=SLATE3, spaceAfter=8),
    ]


# ── Page 1: Cover ─────────────────────────────────────────────────────────────
def _cover_elements():
    # Tiny logo box
    logo = Table(
        [[Paragraph('G', _ps('LogoP', fontSize=18, fontName='Helvetica-Bold',
                              textColor=DARK, alignment=TA_CENTER))]],
        colWidths=[13 * mm], rowHeights=[13 * mm],
    )
    logo.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), WHITE),
        ('ALIGN',      (0, 0), (0, 0), 'CENTER'),
        ('VALIGN',     (0, 0), (0, 0), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (0, 0), 0),
        ('BOTTOMPADDING', (0, 0), (0, 0), 0),
    ]))

    return [
        Spacer(1, 52 * mm),
        logo,
        Spacer(1, 10 * mm),
        Paragraph('Grain &amp; Co.',
                  _ps('CoCo', fontSize=30, fontName='Helvetica-Bold',
                      textColor=WHITE, leading=36, spaceAfter=6)),
        Paragraph('CFO Dashboard Report',
                  _ps('CoTitle', fontSize=17, fontName='Helvetica-Bold',
                      textColor=TEAL2, leading=23, spaceAfter=6)),
        Paragraph('Financial Year 2025',
                  _ps('CoFY', fontSize=13, fontName='Helvetica',
                      textColor=SLATE2, leading=18, spaceAfter=4)),
        Spacer(1, 16 * mm),
        HRFlowable(width=CW, thickness=0.5, color=TEAL, spaceAfter=14 * mm),
        Paragraph(f"Generated {date.today().strftime('%d %B %Y')}",
                  _ps('CoDate', fontSize=10, fontName='Helvetica',
                      textColor=SLATE, leading=14)),
        Spacer(1, 4 * mm),
        Paragraph('Confidential · Internal Use Only',
                  _ps('CoConf', fontSize=9, fontName='Helvetica-Oblique',
                      textColor=SLATE2, leading=13)),
    ]


# ── Page 2: KPI Summary ───────────────────────────────────────────────────────
def _kpi_elements(kpis):
    elems = [
        NextPageTemplate('Content'),
        PageBreak(),
        _h1('Key Performance Indicators'),
        *_section_rule('Financial Summary — FY2025'),
        Spacer(1, 5 * mm),
    ]

    col_w = CW / 4

    def _kpi_cell(label, value_str, sub_str, val_color=DARK):
        inner = Table(
            [
                [Paragraph(label.upper(),
                           _ps(f'KL{label}', fontSize=7.5, fontName='Helvetica-Bold',
                               textColor=SLATE, leading=10))],
                [Paragraph(value_str,
                           _ps(f'KV{label}', fontSize=15, fontName='Helvetica-Bold',
                               textColor=val_color, leading=20))],
                [Paragraph(sub_str,
                           _ps(f'KS{label}', fontSize=8, fontName='Helvetica',
                               textColor=SLATE2, leading=11))],
            ],
            colWidths=[col_w - 8 * mm],
        )
        inner.setStyle(TableStyle([
            ('TOPPADDING',    (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING',   (0, 0), (-1, -1), 0),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ]))
        return inner

    cr = kpis.get('current_ratio', 0)
    de = kpis.get('debt_to_equity', 0)

    blocks = [
        _kpi_cell('Total Revenue',
                  _fmt(kpis.get('total_revenue', 0)),
                  'FY2025 full year'),
        _kpi_cell('Gross Profit',
                  _fmt(kpis.get('gross_profit', 0)),
                  f"{_pct(kpis.get('gross_margin', 0))} margin",
                  _vc(kpis.get('gross_profit', 0))),
        _kpi_cell('EBITDA',
                  _fmt(kpis.get('ebitda', 0)),
                  f"{_pct(kpis.get('ebitda_margin', 0))} margin",
                  _vc(kpis.get('ebitda', 0))),
        _kpi_cell('Net Profit',
                  _fmt(kpis.get('net_profit', 0)),
                  f"{_pct(kpis.get('net_margin', 0))} margin",
                  _vc(kpis.get('net_profit', 0))),
        _kpi_cell('Total Assets',
                  _fmt(kpis.get('total_assets', 0)),
                  'as at 31 Dec 2025'),
        _kpi_cell('Total Equity',
                  _fmt(kpis.get('total_equity', 0)),
                  "shareholders’ equity"),
        _kpi_cell('Current Ratio',
                  f"{float(cr):.2f}x",
                  'current assets / liabilities',
                  GREEN if cr >= 1.5 else (AMBER if cr >= 1.0 else RED)),
        _kpi_cell('Debt / Equity',
                  f"{float(de):.2f}x",
                  'total liabilities / equity',
                  GREEN if de < 1.0 else RED),
    ]

    grid = Table(
        [blocks[0:4], blocks[4:8]],
        colWidths=[col_w] * 4,
        rowHeights=[30 * mm, 30 * mm],
    )
    grid.setStyle(TableStyle([
        ('BOX',          (0, 0), (-1, -1), 0.5, SLATE3),
        ('INNERGRID',    (0, 0), (-1, -1), 0.5, SLATE3),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 5 * mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3 * mm),
        ('TOPPADDING',   (0, 0), (-1, -1), 4 * mm),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4 * mm),
        ('BACKGROUND',   (0, 1), (-1, 1), BGALT),
    ]))
    elems.append(grid)
    return elems


# ── Page 3: AI Financial Insights ─────────────────────────────────────────────
def _ai_insights_elements(bullets):
    elems = [
        PageBreak(),
        _h1('AI Financial Insights'),
        *_section_rule('Generated by Claude AI · For reference only'),
        Spacer(1, 5 * mm),
    ]

    if bullets:
        for i, b in enumerate(bullets[:4], 1):
            elems.append(
                Paragraph(
                    f'<font color="#0d9488">■</font>  {_xe(b)}',
                    _ps(f'Bul{i}', fontSize=10.5, fontName='Helvetica',
                        textColor=DARK, leading=17, spaceAfter=10,
                        leftIndent=14, firstLineIndent=-14),
                )
            )
    else:
        elems.append(
            Paragraph(
                'AI insights were not available at the time of export. '
                'View the live dashboard first to generate them, then re-export.',
                _ps('NoBul', fontSize=10, fontName='Helvetica-Oblique',
                    textColor=SLATE, leading=15),
            )
        )

    elems += [
        Spacer(1, 8 * mm),
        Paragraph(
            'Generated by Claude AI · For reference only · Not financial advice',
            _ps('Disc', fontSize=8, fontName='Helvetica-Oblique',
                textColor=SLATE2, leading=12),
        ),
    ]
    return elems


# ── Page 4: Revenue Analysis ──────────────────────────────────────────────────
def _revenue_elements(rev_df, kpis):
    elems = [
        PageBreak(),
        _h1('Revenue Analysis'),
        *_section_rule('Monthly Revenue by Channel — FY2025'),
        Spacer(1, 3 * mm),
    ]

    ch_cols = [c for c in rev_df.columns if c not in ('Month', 'Total')]
    headers = ['Month'] + ch_cols + ['Total']
    n = len(headers)
    first_w = 22 * mm
    rest_w  = (CW - first_w) / max(n - 1, 1)
    col_ws  = [first_w] + [rest_w] * (n - 1)

    def _hdr(t):
        return Paragraph(_xe(t), _ps(f'RH{t}', fontSize=8.5, fontName='Helvetica-Bold',
                                      textColor=WHITE, alignment=TA_CENTER, leading=11))
    def _cell(t, bold=False, color=DARK, align=TA_LEFT):
        return Paragraph(_xe(t), _ps(f'RC{t}', fontSize=8.5,
                                      fontName='Helvetica-Bold' if bold else 'Helvetica',
                                      textColor=color, alignment=align, leading=11))

    rows = [[_hdr(h) for h in headers]]
    for _, row in rev_df.iterrows():
        r = [_cell(str(row['Month']))]
        for col in ch_cols:
            r.append(_cell(_fmt_full(row.get(col, 0)), align=TA_RIGHT))
        r.append(_cell(_fmt_full(row.get('Total', 0)), bold=True, color=NAVY, align=TA_RIGHT))
        rows.append(r)

    ts = TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  NAVY),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [WHITE, BGALT]),
        ('BOX',           (0, 0), (-1, -1), 0.4, SLATE3),
        ('INNERGRID',     (0, 0), (-1, -1), 0.3, SLATE3),
        ('BACKGROUND',    (-1, 1), (-1, -1), TEAL_BG),
        ('LINEAFTER',     (-2, 0), (-2, -1), 0.8, SLATE3),
    ])
    tbl = Table(rows, colWidths=col_ws, repeatRows=1)
    tbl.setStyle(ts)
    elems.append(tbl)

    # Channel annual summary
    elems += [
        Spacer(1, 8 * mm),
        *_section_rule('Revenue Channel Breakdown — Full Year'),
        Spacer(1, 3 * mm),
    ]

    total_rev = float(kpis.get('total_revenue', 0) or 1)
    sum_rows = [[
        Paragraph('Channel',      _ps('SH1', fontSize=8.5, fontName='Helvetica-Bold', textColor=WHITE)),
        Paragraph('Annual Total', _ps('SH2', fontSize=8.5, fontName='Helvetica-Bold', textColor=WHITE, alignment=TA_RIGHT)),
        Paragraph('% of Revenue', _ps('SH3', fontSize=8.5, fontName='Helvetica-Bold', textColor=WHITE, alignment=TA_RIGHT)),
    ]]
    for col in ch_cols:
        annual = float(rev_df[col].sum())
        sum_rows.append([
            Paragraph(_xe(col),    _ps(f'Sc{col}', fontSize=9, fontName='Helvetica', textColor=DARK)),
            Paragraph(_fmt_full(annual), _ps(f'Sv{col}', fontSize=9, fontName='Helvetica', textColor=DARK, alignment=TA_RIGHT)),
            Paragraph(f"{annual / total_rev * 100:.1f}%", _ps(f'Sp{col}', fontSize=9, fontName='Helvetica', textColor=TEAL, alignment=TA_RIGHT)),
        ])

    stbl = Table(sum_rows, colWidths=[CW * 0.5, CW * 0.25, CW * 0.25])
    stbl.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  TEAL),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [WHITE, BGALT]),
        ('BOX',           (0, 0), (-1, -1), 0.4, SLATE3),
        ('INNERGRID',     (0, 0), (-1, -1), 0.3, SLATE3),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5 * mm),
    ]))
    elems.append(stbl)
    return elems


# ── Page 5: Cost & Profitability ──────────────────────────────────────────────
def _cost_elements(cost_df, kpis):
    elems = [
        PageBreak(),
        _h1('Cost &amp; Profitability'),
        *_section_rule('Monthly Cost Breakdown — FY2025'),
        Spacer(1, 3 * mm),
    ]

    cost_cols = [c for c in cost_df.columns if c not in ('Month', 'Total')]
    headers   = ['Month'] + cost_cols + ['Total']
    n = len(headers)
    first_w = 22 * mm
    rest_w  = (CW - first_w) / max(n - 1, 1)
    col_ws  = [first_w] + [rest_w] * (n - 1)

    def _hdr(t):
        return Paragraph(_xe(t), _ps(f'CH{t}', fontSize=8.5, fontName='Helvetica-Bold',
                                      textColor=WHITE, alignment=TA_CENTER, leading=11))
    def _cell(t, bold=False, color=DARK, align=TA_LEFT):
        return Paragraph(_xe(t), _ps(f'CC{t}', fontSize=8.5,
                                      fontName='Helvetica-Bold' if bold else 'Helvetica',
                                      textColor=color, alignment=align, leading=11))

    rows = [[_hdr(h) for h in headers]]
    for _, row in cost_df.iterrows():
        r = [_cell(str(row['Month']))]
        for col in cost_cols:
            r.append(_cell(_fmt_full(row.get(col, 0)), align=TA_RIGHT))
        r.append(_cell(_fmt_full(row.get('Total', 0)), bold=True, color=RED_DK, align=TA_RIGHT))
        rows.append(r)

    tbl = Table(rows, colWidths=col_ws, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  NAVY),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [WHITE, BGALT]),
        ('BOX',           (0, 0), (-1, -1), 0.4, SLATE3),
        ('INNERGRID',     (0, 0), (-1, -1), 0.3, SLATE3),
        ('BACKGROUND',    (-1, 1), (-1, -1), RED_BG),
    ]))
    elems.append(tbl)

    # P&L summary card
    elems += [
        Spacer(1, 8 * mm),
        *_section_rule('Profitability Summary'),
        Spacer(1, 3 * mm),
    ]

    rev = float(kpis.get('total_revenue', 0) or 1)
    gp  = float(kpis.get('gross_profit',  0))
    eb  = float(kpis.get('ebitda',        0))
    np_ = float(kpis.get('net_profit',    0))

    pl_rows = [
        [
            Paragraph('Metric',   _ps('PLH', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE)),
            Paragraph('Amount',   _ps('PLH2', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE, alignment=TA_RIGHT)),
            Paragraph('% Revenue',_ps('PLH3', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE, alignment=TA_RIGHT)),
        ],
        [
            Paragraph('Gross Profit', _ps('PL1', fontSize=9.5, fontName='Helvetica', textColor=DARK)),
            Paragraph(_fmt_full(gp),  _ps('PL1v', fontSize=9.5, fontName='Helvetica-Bold', textColor=_vc(gp), alignment=TA_RIGHT)),
            Paragraph(_pct(gp / rev * 100), _ps('PL1p', fontSize=9.5, fontName='Helvetica', textColor=_vc(gp), alignment=TA_RIGHT)),
        ],
        [
            Paragraph('EBITDA',   _ps('PL2', fontSize=9.5, fontName='Helvetica', textColor=DARK)),
            Paragraph(_fmt_full(eb),  _ps('PL2v', fontSize=9.5, fontName='Helvetica-Bold', textColor=_vc(eb), alignment=TA_RIGHT)),
            Paragraph(_pct(eb / rev * 100), _ps('PL2p', fontSize=9.5, fontName='Helvetica', textColor=_vc(eb), alignment=TA_RIGHT)),
        ],
        [
            Paragraph('Net Profit After Tax', _ps('PL3', fontSize=9.5, fontName='Helvetica-Bold', textColor=DARK)),
            Paragraph(_fmt_full(np_), _ps('PL3v', fontSize=9.5, fontName='Helvetica-Bold', textColor=_vc(np_), alignment=TA_RIGHT)),
            Paragraph(_pct(np_ / rev * 100), _ps('PL3p', fontSize=9.5, fontName='Helvetica-Bold', textColor=_vc(np_), alignment=TA_RIGHT)),
        ],
    ]

    pl_tbl = Table(pl_rows, colWidths=[CW * 0.5, CW * 0.25, CW * 0.25])
    pl_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  TEAL),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [WHITE, BGALT, GREEN_BG]),
        ('BOX',           (0, 0), (-1, -1), 0.4, SLATE3),
        ('INNERGRID',     (0, 0), (-1, -1), 0.3, SLATE3),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5 * mm),
        ('LINEABOVE',     (0, 3), (-1, 3),  1.0, SLATE),
        ('FONTNAME',      (0, 3), (-1, 3),  'Helvetica-Bold'),
    ]))
    elems.append(pl_tbl)
    return elems


# ── Page 6: Balance Sheet ─────────────────────────────────────────────────────
def _balance_elements(bs, kpis):
    elems = [
        PageBreak(),
        _h1('Balance Sheet Summary'),
        *_section_rule('Key Balance Sheet Figures — as at 31 December 2025'),
        Spacer(1, 3 * mm),
    ]

    # Filter to non-zero items
    items = [(k, v) for k, v in bs.items() if v is not None]
    try:
        items = [(k, v) for k, v in items if abs(float(v)) > 0]
    except (TypeError, ValueError):
        pass

    bs_rows = [[
        Paragraph('Item',   _ps('BSH', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE)),
        Paragraph('Amount', _ps('BSH2', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE, alignment=TA_RIGHT)),
    ]]
    ts_cmds = [
        ('BACKGROUND',    (0, 0), (-1, 0),  NAVY),
        ('BOX',           (0, 0), (-1, -1), 0.4, SLATE3),
        ('INNERGRID',     (0, 0), (-1, -1), 0.3, SLATE3),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5 * mm),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [WHITE, BGALT]),
    ]

    for i, (item, amount) in enumerate(items, 1):
        is_total = 'TOTAL' in str(item).upper() or str(item).lower().startswith('total')
        fname = 'Helvetica-Bold' if is_total else 'Helvetica'
        bs_rows.append([
            Paragraph(_xe(str(item)),
                      _ps(f'BSi{i}', fontSize=9, fontName=fname, textColor=DARK)),
            Paragraph(_fmt_full(amount),
                      _ps(f'BSv{i}', fontSize=9, fontName=fname,
                          textColor=NAVY if is_total else DARK, alignment=TA_RIGHT)),
        ])
        if is_total:
            ts_cmds += [
                ('BACKGROUND', (0, i), (-1, i), BLUE_BG),
                ('FONTNAME',   (0, i), (-1, i), 'Helvetica-Bold'),
            ]

    bs_tbl = Table(bs_rows, colWidths=[CW * 0.65, CW * 0.35])
    bs_tbl.setStyle(TableStyle(ts_cmds))
    elems.append(bs_tbl)

    # Capital structure summary
    elems += [
        Spacer(1, 8 * mm),
        *_section_rule('Capital Structure Summary'),
        Spacer(1, 3 * mm),
    ]

    ta  = float(kpis.get('total_assets',      0) or 1)
    te  = float(kpis.get('total_equity',      0))
    tl  = float(kpis.get('total_liabilities', 0))
    cr  = float(kpis.get('current_ratio',     0))
    de  = float(kpis.get('debt_to_equity',    0))

    cap_rows = [
        [
            Paragraph('Metric',    _ps('CapH', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE)),
            Paragraph('Value',     _ps('CapH2', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE, alignment=TA_RIGHT)),
            Paragraph('Benchmark', _ps('CapH3', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE, alignment=TA_RIGHT)),
        ],
        [
            Paragraph('Total Assets',      _ps('Ca1', fontSize=9.5, fontName='Helvetica', textColor=DARK)),
            Paragraph(_fmt_full(ta),       _ps('Ca1v', fontSize=9.5, fontName='Helvetica', textColor=DARK, alignment=TA_RIGHT)),
            Paragraph('—',            _ps('Ca1b', fontSize=9.5, fontName='Helvetica', textColor=SLATE2, alignment=TA_RIGHT)),
        ],
        [
            Paragraph('Total Equity',      _ps('Ca2', fontSize=9.5, fontName='Helvetica', textColor=DARK)),
            Paragraph(_fmt_full(te),       _ps('Ca2v', fontSize=9.5, fontName='Helvetica-Bold', textColor=_vc(te), alignment=TA_RIGHT)),
            Paragraph(f"{te / ta * 100:.1f}% of assets", _ps('Ca2b', fontSize=9.5, fontName='Helvetica-Oblique', textColor=SLATE2, alignment=TA_RIGHT)),
        ],
        [
            Paragraph('Total Liabilities', _ps('Ca3', fontSize=9.5, fontName='Helvetica', textColor=DARK)),
            Paragraph(_fmt_full(tl),       _ps('Ca3v', fontSize=9.5, fontName='Helvetica', textColor=SLATE, alignment=TA_RIGHT)),
            Paragraph(f"{tl / ta * 100:.1f}% of assets", _ps('Ca3b', fontSize=9.5, fontName='Helvetica-Oblique', textColor=SLATE2, alignment=TA_RIGHT)),
        ],
        [
            Paragraph('Current Ratio',     _ps('Ca4', fontSize=9.5, fontName='Helvetica', textColor=DARK)),
            Paragraph(f"{cr:.2f}x",        _ps('Ca4v', fontSize=9.5, fontName='Helvetica-Bold',
                                               textColor=GREEN if cr >= 1.5 else (AMBER if cr >= 1.0 else RED), alignment=TA_RIGHT)),
            Paragraph('1.2x – 2.0x ideal', _ps('Ca4b', fontSize=9.5, fontName='Helvetica-Oblique', textColor=SLATE2, alignment=TA_RIGHT)),
        ],
        [
            Paragraph('Debt / Equity',     _ps('Ca5', fontSize=9.5, fontName='Helvetica', textColor=DARK)),
            Paragraph(f"{de:.2f}x",        _ps('Ca5v', fontSize=9.5, fontName='Helvetica-Bold',
                                               textColor=GREEN if de < 1.0 else RED, alignment=TA_RIGHT)),
            Paragraph('&lt; 1.0x preferred', _ps('Ca5b', fontSize=9.5, fontName='Helvetica-Oblique', textColor=SLATE2, alignment=TA_RIGHT)),
        ],
    ]

    cap_tbl = Table(cap_rows, colWidths=[CW * 0.45, CW * 0.28, CW * 0.27])
    cap_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  TEAL),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [WHITE, BGALT, WHITE, BGALT, WHITE]),
        ('BOX',           (0, 0), (-1, -1), 0.4, SLATE3),
        ('INNERGRID',     (0, 0), (-1, -1), 0.3, SLATE3),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5 * mm),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5 * mm),
    ]))
    elems.append(cap_tbl)
    return elems


# ── Public entry point ────────────────────────────────────────────────────────
def build_cfo_pdf(kpis, rev_df, cost_df, pl, bs, ai_bullets=None):
    """
    Build the 6-page CFO PDF.
    Returns a BytesIO buffer at position 0, ready to send as a file download.

    Args:
        kpis       – dict from _build_dashboard_data()
        rev_df     – revenue DataFrame
        cost_df    – costs DataFrame
        pl         – P&L dict (Item → Amount)
        bs         – balance sheet dict (Item → Amount)
        ai_bullets – list[str] | None
    """
    buf = BytesIO()
    doc = _make_doc(buf)

    elements = (
        _cover_elements()
        + _kpi_elements(kpis)
        + _ai_insights_elements(ai_bullets)
        + _revenue_elements(rev_df, kpis)
        + _cost_elements(cost_df, kpis)
        + _balance_elements(bs, kpis)
    )

    doc.build(elements)
    buf.seek(0)
    return buf
