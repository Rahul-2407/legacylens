"""Reusable HTML builders in the assessment-ledger visual language.

Pure string functions (no Streamlit) so they stay unit-testable; the
views render them with components.html or st.markdown.
"""

import html

from dashboard.theme import (
    BODY, BORDER, CARD, DISPLAY, INK, INK_SOFT, MONO, MUTED,
    PAPER_ON_INK, SEVERITY, SEVERITY_BG, STATUS, SUBTLE,
)


def status_pill(status: str) -> str:
    fg, bg = STATUS.get(status, ("#6E685C", SUBTLE))
    return (f'<span style="background:{bg};color:{fg};padding:3px 12px;'
            f'border-radius:999px;font-size:12px;font-weight:600;'
            f'font-family:{BODY};letter-spacing:0.01em">'
            f'{html.escape(status)}</span>')


def severity_pill(severity: str) -> str:
    fg = SEVERITY.get(severity, MUTED)
    bg = SEVERITY_BG.get(severity, SUBTLE)
    return (f'<span style="background:{bg};color:{fg};padding:2px 10px;'
            f'border-radius:999px;font-size:10.5px;font-weight:600;'
            f'font-family:{BODY};text-transform:uppercase;'
            f'letter-spacing:0.06em">{html.escape(severity)}</span>')


def stat(value: str, label: str) -> str:
    return (
        f'<div style="flex:1;min-width:130px;padding:22px 24px;'
        f'background:{CARD};border:1px solid {BORDER};border-radius:16px">'
        f'<div style="font-family:{DISPLAY};font-size:32px;font-weight:500;'
        f'letter-spacing:-0.02em;color:{INK};line-height:1.1">'
        f'{html.escape(value)}</div>'
        f'<div style="font-size:12px;color:{MUTED};margin-top:6px;'
        f'text-transform:uppercase;letter-spacing:0.06em;font-weight:500">'
        f'{html.escape(label)}</div></div>'
    )


def verdict_plate(value: str, label: str) -> str:
    """The signature element: the readiness verdict set on a black plate."""
    return (
        f'<div style="flex:1.2;min-width:170px;padding:22px 24px;'
        f'background:{INK};border:1px solid {INK};border-radius:16px">'
        f'<div style="font-family:{DISPLAY};font-size:38px;font-weight:500;'
        f'letter-spacing:-0.02em;color:{PAPER_ON_INK};line-height:1.05">'
        f'{html.escape(value)}</div>'
        f'<div style="font-size:12px;color:#A8A294;margin-top:6px;'
        f'text-transform:uppercase;letter-spacing:0.06em;font-weight:500">'
        f'{html.escape(label)}</div></div>'
    )


def hero(stats: list[tuple[str, str]]) -> str:
    """KPI strip. The first stat is the verdict and gets the black plate;
    the rest sit on white."""
    if not stats:
        return ""
    first, *rest = stats
    cells = verdict_plate(*first) + "".join(stat(v, l) for v, l in rest)
    return (
        f'<div style="display:flex;gap:14px;flex-wrap:wrap;'
        f'align-items:stretch;margin-bottom:26px;font-family:{BODY}">'
        f'{cells}</div>'
    )


def section_marker(title: str, note: str = "") -> str:
    """Ledger-style section header: a small ink square + spaced label."""
    note_html = (f'<span style="font-size:12px;color:{MUTED};'
                 f'font-weight:400;letter-spacing:0">'
                 f'{html.escape(note)}</span>') if note else ""
    return (
        f'<div style="display:flex;align-items:baseline;gap:10px;'
        f'margin:30px 0 14px;font-family:{BODY}">'
        f'<span style="width:9px;height:9px;background:{INK};'
        f'border-radius:2px;display:inline-block;align-self:center"></span>'
        f'<span style="font-family:{DISPLAY};font-size:17px;'
        f'font-weight:600;color:{INK};letter-spacing:-0.01em">'
        f'{html.escape(title)}</span>{note_html}</div>'
    )


def finding_card(finding: dict) -> str:
    sev = finding["severity"]
    ev = finding.get("evidence") or [{}]
    first = ev[0] if ev else {}
    where = first.get("file_path") or first.get("reference_url") or ""
    line = f":{first['line_start']}" if first.get("line_start") else ""
    loc = html.escape(f"{where}{line}") if where else ""
    accent = SEVERITY.get(sev, MUTED)
    return (
        f'<div style="background:{CARD};border:1px solid {BORDER};'
        f'border-left:3px solid {accent};border-radius:12px;'
        f'padding:14px 16px;margin-bottom:10px">'
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:center;margin-bottom:8px;gap:8px">'
        f'{severity_pill(sev)}'
        f'<span style="font-size:11px;color:{MUTED};font-family:{MONO}">'
        f'{html.escape(finding["rule_id"])}</span></div>'
        f'<div style="font-size:13.5px;font-weight:600;color:{INK};'
        f'margin-bottom:6px;line-height:1.4;font-family:{BODY}">'
        f'{html.escape(finding["title"])}</div>'
        + (f'<div style="font-size:11px;color:{MUTED};font-family:{MONO};'
           f'word-break:break-all">{loc}</div>' if loc else "")
        + '</div>'
    )


def column(title: str, count: int, cards_html: str) -> str:
    return (
        f'<div style="flex:1;min-width:240px">'
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:center;margin-bottom:12px;padding:0 2px">'
        f'<span style="font-size:12px;font-weight:600;color:{INK_SOFT};'
        f'font-family:{BODY};text-transform:uppercase;'
        f'letter-spacing:0.07em">{html.escape(title)}</span>'
        f'<span style="background:{SUBTLE};color:{INK_SOFT};'
        f'border-radius:8px;padding:1px 9px;font-size:12px;'
        f'font-weight:600;font-family:{BODY}">{count}</span></div>'
        f'{cards_html}</div>'
    )


def severity_row(sev: str, label: str, items: list[dict]) -> str:
    """One severity band: a header line, then cards in a 4-across grid."""
    cards = "".join(finding_card(f) for f in items)
    return (
        f'<div style="margin-bottom:22px">'
        f'<div style="display:flex;align-items:center;gap:10px;'
        f'margin-bottom:12px">'
        f'{severity_pill(sev)}'
        f'<span style="font-size:12px;color:{MUTED};font-family:{BODY}">'
        f'{len(items)} finding{"s" if len(items) != 1 else ""}</span>'
        f'<span style="flex:1;border-bottom:1px solid {BORDER}"></span>'
        f'</div>'
        f'<div style="display:grid;'
        f'grid-template-columns:repeat(auto-fill,minmax(250px,1fr));'
        f'gap:12px;align-items:start">{cards}</div></div>'
    )


def board(findings: list[dict]) -> str:
    """Severity board: one row per severity in triage order, worst first.
    Empty severities are skipped to keep the page short."""
    order = ["critical", "high", "medium", "low"]
    labels = {"critical": "Critical", "high": "High",
              "medium": "Medium", "low": "Low"}
    rows = []
    for sev in order:
        items = [f for f in findings if f["severity"] == sev]
        if items:
            rows.append(severity_row(sev, labels[sev], items))
    if not rows:
        return (f'<div style="color:{MUTED};font-size:13px;padding:14px;'
                f'font-family:{BODY};border:1px dashed {BORDER};'
                f'border-radius:12px;text-align:center">'
                f'No findings at these severities.</div>')
    return (f'<div style="font-family:{BODY}">' + "".join(rows) + '</div>')


def risk_matrix(matrix_df) -> str:
    """Severity-by-category risk matrix as a themed HTML table.

    Cells are tinted with the row's severity color, intensity scaled by
    count; row and column totals frame the grid. Replaces the plotly
    heatmap with something that reads like part of the product.
    """
    severities = list(matrix_df.index)
    categories = list(matrix_df.columns)
    grid_max = max(int(matrix_df.values.max()), 1)

    def cell(sev: str, count: int) -> str:
        color = SEVERITY.get(sev, MUTED)
        if count == 0:
            return (f'<td style="padding:0;border:1px solid {BORDER}">'
                    f'<div style="padding:14px 8px;text-align:center;'
                    f'color:{BORDER};font-size:13px">&mdash;</div></td>')
        alpha = 0.14 + 0.66 * (count / grid_max)
        return (
            f'<td style="padding:0;border:1px solid {BORDER}">'
            f'<div style="padding:12px 8px;text-align:center;'
            f'background:color-mix(in srgb, {color} {alpha:.0%}, white);'
            f'color:{"#FFFFFF" if alpha > 0.55 else color};'
            f'font-family:{DISPLAY};font-size:17px;font-weight:600">'
            f'{count}</div></td>')

    def total_chip(n: int, sev: str | None = None) -> str:
        color = SEVERITY.get(sev, INK) if sev else INK
        return (f'<span style="font-family:{DISPLAY};font-size:14px;'
                f'font-weight:600;color:{color}">{n}</span>')

    head = "".join(
        f'<th style="padding:10px 8px;font-size:11px;color:{MUTED};'
        f'font-weight:600;text-transform:uppercase;'
        f'letter-spacing:0.06em;border:1px solid {BORDER};'
        f'background:{SUBTLE}">{c.replace("_", " ")}</th>'
        for c in categories)

    body_rows = []
    for sev in severities:
        counts = [int(matrix_df.loc[sev, c]) for c in categories]
        cells = "".join(cell(sev, n) for n in counts)
        body_rows.append(
            f'<tr><th style="padding:10px 14px;text-align:left;'
            f'border:1px solid {BORDER};background:{SUBTLE}">'
            f'{severity_pill(sev)}</th>{cells}'
            f'<td style="padding:10px;text-align:center;'
            f'border:1px solid {BORDER}">{total_chip(sum(counts), sev)}'
            f'</td></tr>')

    col_totals = "".join(
        f'<td style="padding:10px;text-align:center;'
        f'border:1px solid {BORDER}">'
        f'{total_chip(int(matrix_df[c].sum()))}</td>'
        for c in categories)
    grand = int(matrix_df.values.sum())

    return (
        f'<div style="background:{CARD};border:1px solid {BORDER};'
        f'border-radius:16px;padding:18px;font-family:{BODY};'
        f'overflow-x:auto">'
        f'<table style="border-collapse:collapse;width:100%;'
        f'font-family:{BODY}">'
        f'<tr><th style="padding:10px;border:1px solid {BORDER};'
        f'background:{SUBTLE};font-size:11px;color:{MUTED};'
        f'font-weight:600;text-transform:uppercase;'
        f'letter-spacing:0.06em;text-align:left">Severity</th>'
        f'{head}'
        f'<th style="padding:10px 8px;font-size:11px;color:{MUTED};'
        f'font-weight:600;text-transform:uppercase;'
        f'letter-spacing:0.06em;border:1px solid {BORDER};'
        f'background:{SUBTLE}">Total</th></tr>'
        + "".join(body_rows) +
        f'<tr><th style="padding:10px 14px;text-align:left;'
        f'border:1px solid {BORDER};background:{SUBTLE};font-size:11px;'
        f'color:{MUTED};font-weight:600;text-transform:uppercase;'
        f'letter-spacing:0.06em">Total</th>{col_totals}'
        f'<td style="padding:10px;text-align:center;'
        f'border:1px solid {BORDER};background:{INK}">'
        f'<span style="font-family:{DISPLAY};font-size:15px;'
        f'font-weight:600;color:{PAPER_ON_INK}">{grand}</span>'
        f'</td></tr></table>'
        f'<div style="font-size:11.5px;color:{MUTED};margin-top:12px;'
        f'font-family:{BODY}">Cell color deepens with the number of '
        f'findings. Rows are ordered by severity; the bottom-right '
        f'figure is the total across all findings.</div></div>'
    )
