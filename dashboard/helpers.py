"""Pure display helpers — testable without Streamlit."""

import pandas as pd

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
SEVERITY_COLOR = {
    "critical": "#A03225",
    "high": "#B26424",
    "medium": "#96781C",
    "low": "#3D6478",
    "info": "#8C8577",
}


def severity_badge(severity: str) -> str:
    color = SEVERITY_COLOR.get(severity, "#95a5a6")
    return (f'<span style="background:{color};color:white;'
            f'padding:2px 10px;border-radius:10px;font-size:0.8em">'
            f'{severity.upper()}</span>')


def risk_matrix_df(scorecard: dict) -> pd.DataFrame:
    """severity × category counts as a dataframe, severity rows ordered."""
    matrix = scorecard.get("risk_matrix", {})
    categories = sorted({c for row in matrix.values() for c in row})
    rows = [s for s in SEVERITY_ORDER if s in matrix]
    data = [[matrix[s].get(c, 0) for c in categories] for s in rows]
    return pd.DataFrame(data, index=rows, columns=categories)


def findings_df(findings: list[dict]) -> pd.DataFrame:
    rows = [{
        "severity": f["severity"],
        "rule": f["rule_id"],
        "title": f["title"],
        "id": f["finding_id"],
    } for f in findings]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["severity_rank"] = df["severity"].map(
            {s: i for i, s in enumerate(SEVERITY_ORDER)})
        df = df.sort_values(["severity_rank", "rule"]).drop(
            columns="severity_rank")
    return df


def _parse_waves(diagram: str):
    """Extract (wave_title, [module_labels]) from a flowchart LR waves diagram."""
    import re
    waves, current = [], None
    for line in diagram.splitlines():
        s = line.strip()
        m = re.match(r'subgraph\s+\w+\["(.+?)"\]', s)
        if m:
            current = (m.group(1), [])
            waves.append(current)
        elif s == "end":
            current = None
        elif current is not None:
            n = re.match(r'\w+\["(.+?)"\]', s)
            if n:
                current[1].append(n.group(1))
    return waves


def waves_svg(diagram: str) -> str:
    """Render the waves diagram as a standalone SVG, one wide row per
    wave with modules in a 4-across grid, waves flowing downward.

    Server-side rendering (no JS/CDN) sidesteps the Streamlit component
    iframe where external scripts are blocked by CSP. The wide-row
    layout keeps the page short even for waves with many modules.
    """
    import html as _html
    import math
    waves = _parse_waves(diagram)
    if not waves:
        return "<p>No wave structure available.</p>"

    W = 1000
    box_x, box_w = 10, 980
    cols, chip_h, chip_gap = 4, 30, 10
    pad, title_h, bottom_pad, wave_gap, top = 18, 36, 16, 26, 12
    chip_w = (box_w - 2 * pad - (cols - 1) * chip_gap) / cols
    max_chars = 34

    def clip(text: str) -> str:
        if len(text) <= max_chars:
            return text
        keep = max_chars - 1
        head = keep // 2
        return text[:head] + "\u2026" + text[-(keep - head):]

    box_heights = []
    for _, mods in waves:
        rows = max(1, math.ceil(len(mods) / cols))
        box_heights.append(title_h + rows * (chip_h + chip_gap)
                           - chip_gap + bottom_pad)
    total_h = top + sum(box_heights) + wave_gap * (len(waves) - 1) + top

    # Ledger palette: first wave in ink, middle waves in slate,
    # final wave in the critical accent - beige-compatible throughout.
    palette = ["#141412", "#3D6478", "#3D6478", "#3D6478",
               "#3D6478", "#8C8577", "#8C8577", "#A03225"]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
        f'height="{total_h}" viewBox="0 0 {W} {total_h}" '
        f'style="max-width:100%;height:auto;background:#FFFFFF;'
        f'border:1px solid #DDD6C8;border-radius:12px;display:block">',
        '<defs><marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto"><path d="M2 1L8 5L2 9" '
        'fill="none" stroke="#8C8577" stroke-width="1.5"/></marker></defs>',
    ]

    cx = box_x + box_w / 2
    y = top
    for i, (title, mods) in enumerate(waves):
        color = palette[i % len(palette)] if i < len(waves) - 1 else "#A03225"
        if i == 0:
            color = "#141412"
        bh = box_heights[i]
        parts.append(
            f'<rect x="{box_x}" y="{y}" width="{box_w}" height="{bh}" '
            f'rx="12" fill="{color}" fill-opacity="0.05" stroke="{color}" '
            f'stroke-width="1"/>')
        parts.append(
            f'<text x="{box_x + pad}" y="{y + 23}" text-anchor="start" '
            f'font-family="sans-serif" font-size="14" font-weight="600" '
            f'fill="{color}">{_html.escape(title)}</text>')
        for j, mod in enumerate(mods):
            row, col = divmod(j, cols)
            chip_x = box_x + pad + col * (chip_w + chip_gap)
            chip_y = y + title_h + row * (chip_h + chip_gap)
            parts.append(
                f'<rect x="{chip_x:.1f}" y="{chip_y}" '
                f'width="{chip_w:.1f}" height="{chip_h}" rx="8" '
                f'fill="#FFFFFF" stroke="{color}" stroke-opacity="0.45" '
                f'stroke-width="1"/>')
            parts.append(
                f'<text x="{chip_x + chip_w / 2:.1f}" '
                f'y="{chip_y + chip_h / 2 + 3.5}" text-anchor="middle" '
                f'font-family="monospace" font-size="10.5" '
                f'fill="#3D3A33">{_html.escape(clip(mod))}</text>')
        if i < len(waves) - 1:
            parts.append(
                f'<line x1="{cx}" y1="{y + bh}" x2="{cx}" '
                f'y2="{y + bh + wave_gap}" stroke="#8C8577" '
                f'stroke-width="1.5" marker-end="url(#ar)"/>')
        y += bh + wave_gap

    parts.append("</svg>")
    return "".join(parts)


def mermaid_html(diagram: str, height: int = 480) -> str:
    """Kept for the module-graph diagram; waves use waves_svg (JS-free)."""
    import json
    payload = json.dumps(diagram)
    return f"""
<div id="mmd" style="text-align:center;background:white;padding:8px"></div>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js">
</script>
<script>
  const source = {payload};
  function draw() {{
    if (!window.mermaid) {{ setTimeout(draw, 100); return; }}
    window.mermaid.initialize({{ startOnLoad: false, theme: 'neutral' }});
    const el = document.getElementById('mmd');
    el.innerHTML = '<pre class="mermaid">' + source + '</pre>';
    try {{ window.mermaid.run({{ nodes: [el.querySelector('.mermaid')] }}); }}
    catch (e) {{ el.textContent = 'Diagram render error: ' + e.message; }}
  }}
  draw();
</script>
"""


def _parse_module_graph(diagram: str):
    """Extract nodes, edges, and cycle members from the module-graph
    mermaid source (the deterministic format from artifacts/mermaid.py)."""
    import re
    nodes: dict[str, str] = {}
    edges: list[tuple[str, str]] = []
    cycles: set[str] = set()
    for line in diagram.splitlines():
        s = line.strip()
        m = re.match(r'^(\w+)\["(.+?)"\]$', s)
        if m:
            nodes[m.group(1)] = m.group(2)
            continue
        m = re.match(r'^(\w+)\s*-->\s*(\w+)$', s)
        if m:
            edges.append((m.group(1), m.group(2)))
            continue
        m = re.match(r'^class\s+([\w,]+)\s+cycle$', s)
        if m:
            cycles.update(m.group(1).split(","))
    return nodes, edges, cycles


def modules_dot(diagram: str) -> str:
    """Convert the module-graph mermaid source to themed Graphviz DOT.

    Rendered with st.graphviz_chart, which uses Streamlit's bundled
    frontend renderer - no CDN scripts, so it works behind CSP and
    ad/script blockers where the Mermaid iframe approach fails.
    """
    nodes, edges, cycles = _parse_module_graph(diagram)

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    lines = [
        "digraph modules {",
        '  rankdir=TB;',
        '  bgcolor="transparent";',
        '  nodesep=0.35; ranksep=0.5;',
        '  node [shape=box, style="rounded,filled", fillcolor="#FFFFFF",'
        ' color="#DDD6C8", fontname="Courier", fontsize=10,'
        ' fontcolor="#3D3A33", margin="0.16,0.09"];',
        '  edge [color="#8C8577", arrowsize=0.6, penwidth=0.8];',
    ]
    for nid, label in nodes.items():
        if nid == "legend":
            lines.append(
                f'  {nid} [label="{esc(label)}", shape=plaintext,'
                f' fillcolor="transparent", fontcolor="#8C8577",'
                f' fontsize=9];')
        elif nid in cycles:
            lines.append(
                f'  {nid} [label="{esc(label)}", fillcolor="#F3E4E1",'
                f' color="#A03225", fontcolor="#A03225", penwidth=1.4];')
        else:
            lines.append(f'  {nid} [label="{esc(label)}"];')
    for src, dst in edges:
        lines.append(f"  {src} -> {dst};")
    lines.append("}")
    return "\n".join(lines)
