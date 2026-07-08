"""Projects view: upload a codebase, watch the portfolio of analyses."""

import html as _html

import streamlit as st
import streamlit.components.v1 as components

from dashboard.api_client import ApiClient, DashboardApiError
from dashboard.components import section_marker, status_pill
from dashboard.theme import (
    BODY, BORDER, CARD, DISPLAY, INK, MONO, MUTED, SUBTLE,
)


def _project_row(p: dict) -> str:
    readiness = p.get("readiness")
    r_txt = f"{readiness}/100" if readiness is not None else "&mdash;"
    files = p.get("file_count") or "&mdash;"
    return (
        f'<div style="display:flex;align-items:center;gap:16px;'
        f'padding:15px 22px;border-bottom:1px solid {BORDER};'
        f'font-family:{BODY}">'
        f'<div style="flex:2;min-width:0">'
        f'<div style="font-weight:600;color:{INK};font-size:14px">'
        f'{_html.escape(p["name"])}</div>'
        f'<div style="font-size:11px;color:{MUTED};font-family:{MONO};'
        f'margin-top:2px">{_html.escape(p["project_id"])}</div></div>'
        f'<div style="flex:1">{status_pill(p["status"])}</div>'
        f'<div style="flex:1;color:{MUTED};font-size:13px">'
        f'{files} files</div>'
        f'<div style="flex:1;color:{INK};font-size:15px;font-weight:600;'
        f'font-family:{DISPLAY}">{r_txt}</div></div>'
    )


def render(client: ApiClient) -> None:
    st.markdown(
        f'<div style="font-size:11px;color:{MUTED};font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.1em;'
        f'font-family:{BODY};margin-bottom:6px">'
        f'Migration analysis platform</div>'
        f'<div style="font-family:{DISPLAY};font-size:34px;'
        f'font-weight:600;letter-spacing:-0.02em;color:{INK};'
        f'line-height:1.15;margin-bottom:10px">LegacyLens</div>'
        f'<div style="font-size:14.5px;color:{MUTED};line-height:1.7;'
        f'max-width:780px;font-family:{BODY};margin-bottom:14px">'
        f'LegacyLens assesses how ready a legacy codebase is for '
        f'migration. Upload a source archive and a pipeline of '
        f'deterministic analyzers inventories the technology stack, maps '
        f'internal module dependencies, and surfaces end-of-life '
        f'frameworks, vulnerable dependencies, database coupling, and '
        f'structural risk. Every finding is traced to a file, a line, or '
        f'a reference, so nothing in the assessment rests on '
        f'guesswork.</div>'
        f'<div style="font-size:14.5px;color:{MUTED};line-height:1.7;'
        f'max-width:780px;font-family:{BODY};margin-bottom:8px">'
        f'The output is a decision-ready package: a readiness score with '
        f'a transparent breakdown of what moved it, an effort estimate '
        f'in person-days, a wave-by-wave migration plan ordered by '
        f'dependency, and a full written report in which every claim '
        f'cites its evidence. Static analysis produces the facts; AI '
        f'contributes only cited interpretation on top of them.</div>'
        f'<div style="border-bottom:1px solid {BORDER};'
        f'margin:18px 0 26px"></div>',
        unsafe_allow_html=True)

    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0

    with st.container(border=True):
        st.markdown(
            f'<div style="font-weight:600;color:{INK};font-size:15px;'
            f'font-family:{DISPLAY};margin-bottom:2px">'
            f'Analyze a project</div>'
            f'<div style="font-size:12.5px;color:{MUTED};'
            f'margin-bottom:12px;font-family:{BODY}">'
            f'Accepted format: a .zip archive of the source tree.</div>',
            unsafe_allow_html=True)
        upload = st.file_uploader(
            "Source archive (.zip)", type=["zip"],
            label_visibility="collapsed",
            key=f"uploader_{st.session_state.uploader_key}")
        if upload is not None and st.button("Start analysis",
                                            type="primary"):
            try:
                created = client.upload(upload.name, upload.getvalue())
                st.success(f"Analysis queued: {created['project_id']}")
                st.session_state.uploader_key += 1
                st.rerun()
            except DashboardApiError as exc:
                st.error(str(exc))

    try:
        projects = client.list_projects()
    except DashboardApiError as exc:
        st.error(str(exc))
        return
    if not projects:
        st.info("No projects yet. Upload an archive above to begin.")
        return

    active = [p for p in projects if p["status"] in ("pending", "running")]

    note = (f"{len(active)} in progress, updating automatically"
            if active else f"{len(projects)} total")
    st.markdown(section_marker("Projects", note), unsafe_allow_html=True)

    header = (
        f'<div style="display:flex;gap:16px;padding:12px 22px;'
        f'background:{SUBTLE};font-size:11px;color:{MUTED};'
        f'font-weight:600;text-transform:uppercase;'
        f'letter-spacing:0.07em;font-family:{BODY}">'
        f'<div style="flex:2">Project</div><div style="flex:1">Status</div>'
        f'<div style="flex:1">Size</div>'
        f'<div style="flex:1">Readiness</div></div>')
    rows = "".join(_project_row(p) for p in projects)
    table_h = 46 + len(projects) * 72 + 20
    components.html(
        f'<div style="background:{CARD};border:1px solid {BORDER};'
        f'border-radius:16px;overflow:hidden;font-family:{BODY}">'
        f'{header}{rows}</div>',
        height=table_h, scrolling=False)

    with st.expander("Delete a project"):
        ids = [p["project_id"] for p in projects]
        target = st.selectbox("Project to delete", ids, key="del_target")
        confirm = st.checkbox(
            f"Yes, permanently delete {target} and all its findings",
            key="confirm_delete")
        if st.button("Delete", disabled=not confirm):
            try:
                client.delete(target)
                st.success(f"Deleted {target}.")
                st.rerun()
            except DashboardApiError as exc:
                st.error(str(exc))

    import os
    if (active and not os.environ.get("STREAMLIT_TEST_MODE")):
        import time
        time.sleep(3)
        st.rerun()
