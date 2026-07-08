"""Project detail: the assessment dashboard for one analyzed project."""

import streamlit as st
import streamlit.components.v1 as components

from dashboard.api_client import ApiClient, DashboardApiError
from dashboard.components import board, hero, risk_matrix, section_marker
from dashboard.helpers import SEVERITY_ORDER, risk_matrix_df, waves_svg
from dashboard.theme import BODY, DISPLAY, INK, MUTED


def render(client: ApiClient) -> None:
    try:
        projects = [p for p in client.list_projects()
                    if p["status"] == "completed"]
    except DashboardApiError as exc:
        st.error(str(exc))
        return
    if not projects:
        st.info("No completed analyses yet.")
        return

    labels = {f"{p['project_id']} \u2014 {p['name']}": p for p in projects}
    choice = st.selectbox("Completed analyses", list(labels))
    project = labels[choice]
    pid = project["project_id"]

    scorecard = client.scorecard(pid)
    findings = client.findings(pid)
    effort = scorecard["effort"]

    st.markdown(
        f'<div style="font-family:{DISPLAY};font-size:28px;'
        f'font-weight:600;letter-spacing:-0.02em;color:{INK};'
        f'margin:8px 0 4px">{project["name"]}</div>'
        f'<div style="font-size:13px;color:{MUTED};margin-bottom:22px;'
        f'font-family:{BODY}">Assessment summary</div>',
        unsafe_allow_html=True)

    crit = sum(1 for f in findings if f["severity"] == "critical")
    high = sum(1 for f in findings if f["severity"] == "high")
    st.markdown(hero([
        (f'{scorecard["readiness"]["value"]}/100', "Migration readiness"),
        (str(len(findings)), "Total findings"),
        (str(crit + high), "Critical and high"),
        (f'{effort["expected_days"]}', "Effort (person-days)"),
        (str(project.get("file_count") or "\u2014"), "Files analyzed"),
    ]), unsafe_allow_html=True)

    breakdown = scorecard["readiness"]["components"]
    if breakdown:
        with st.expander("Why this readiness score"):
            for c in breakdown:
                st.markdown(f"**{c['delta']:+d}** \u2014 {c['detail']}")

    st.markdown(section_marker("Findings by severity"),
                unsafe_allow_html=True)
    items = [f for f in findings if f["severity"] != "info"]
    components.html(
        f'<div style="font-family:{BODY}">{board(items)}</div>',
        height=_board_height(items), scrolling=True)

    st.markdown(section_marker("Risk matrix",
                               "severity by category"),
                unsafe_allow_html=True)
    df = risk_matrix_df(scorecard)
    if not df.empty:
        matrix_h = 96 + (len(df.index) + 1) * 50 + 60
        components.html(risk_matrix(df), height=matrix_h, scrolling=True)

    modules = client.artifact(pid, "mermaid_modules")
    waves = client.artifact(pid, "mermaid_waves")
    if modules or waves:
        st.markdown(section_marker("Architecture"), unsafe_allow_html=True)
        tabs = st.tabs(["Migration waves", "Module dependencies"])
        with tabs[0]:
            if waves:
                svg = waves_svg(waves)
                import re
                m = re.search(r'height="(\d+)"', svg)
                h = int(m.group(1)) if m else 600
                components.html(
                    f'<div style="display:flex;justify-content:center">'
                    f'{svg}</div>', height=h + 20, scrolling=True)
            else:
                st.info("No wave structure available.")
        with tabs[1]:
            if modules:
                from dashboard.helpers import modules_dot
                st.graphviz_chart(modules_dot(modules),
                                  use_container_width=True)
                st.caption("Modules highlighted in red participate in an "
                           "import cycle. Lower-coupling modules may be "
                           "omitted for readability.")
            else:
                st.info("No internal import edges detected.")

    st.markdown(section_marker("Detailed findings"),
                unsafe_allow_html=True)
    sev_filter = st.multiselect(
        "Severity", SEVERITY_ORDER,
        default=[s for s in ("critical", "high", "medium")
                 if any(f["severity"] == s for f in findings)])
    for f in sorted([x for x in findings if x["severity"] in sev_filter],
                    key=lambda x: SEVERITY_ORDER.index(x["severity"])):
        with st.expander(f"{f['rule_id']} \u2014 {f['title']}"):
            st.write(f["description"])
            st.markdown("**Evidence**")
            for ev in f["evidence"]:
                where = ev.get("file_path") or ev.get("reference_url") or ""
                line = f":{ev['line_start']}" if ev.get("line_start") else ""
                snippet = f" \u2014 `{ev['snippet']}`" if ev.get("snippet") else ""
                detail = f" ({ev['detail']})" if ev.get("detail") else ""
                st.markdown(f"- `{where}{line}`{snippet}{detail}")

    st.markdown(section_marker("Reports"), unsafe_allow_html=True)
    report = client.report(pid)
    st.download_button("Download assessment report (Markdown)", data=report,
                       file_name=f"{pid}-assessment.md",
                       mime="text/markdown")
    with st.expander("Preview report"):
        st.markdown(report)


def _board_height(items: list) -> int:
    """Rows layout: per non-empty severity, a header line plus
    ceil(n / 4) card rows at ~118px each."""
    import math
    from collections import Counter
    c = Counter(f["severity"] for f in items)
    total = 20
    for s in ("critical", "high", "medium", "low"):
        n = c.get(s, 0)
        if n:
            total += 42 + math.ceil(n / 4) * 118 + 16
    return max(total, 120)
