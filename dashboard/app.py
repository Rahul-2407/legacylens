"""LegacyLens dashboard entry point.

Run:  streamlit run dashboard/app.py
Env:  LEGACYLENS_API_URL (default http://localhost:8000)
"""

import streamlit as st

from dashboard.bootstrap import ensure_standalone_api

ensure_standalone_api()

from dashboard.api_client import ApiClient
from dashboard.theme import BODY, BORDER, DISPLAY, INK, MONO, MUTED, base_css
from dashboard.views import project_detail, projects

st.set_page_config(page_title="LegacyLens", layout="wide")
st.markdown(base_css(), unsafe_allow_html=True)

client = ApiClient()

with st.sidebar:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;'
        f'margin-bottom:2px">'
        f'<span style="width:12px;height:12px;background:{INK};'
        f'border-radius:3px;display:inline-block"></span>'
        f'<span style="font-family:{DISPLAY};font-size:21px;'
        f'font-weight:700;letter-spacing:-0.02em;color:{INK}">'
        f'LegacyLens</span></div>'
        f'<div style="font-size:12px;color:{MUTED};margin:2px 0 24px;'
        f'font-family:{BODY}">Migration analysis platform</div>',
        unsafe_allow_html=True)

    page = st.radio("Navigation", ["Projects", "Project detail"],
                    label_visibility="collapsed")

    st.divider()

    ok = client.health()
    dot = "#2E5F41" if ok else "#A03225"
    label = "API connected" if ok else "API unreachable"
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;'
        f'font-family:{BODY}">'
        f'<span style="width:7px;height:7px;border-radius:50%;'
        f'background:{dot};display:inline-block"></span>'
        f'<span style="font-size:12.5px;color:{INK};font-weight:500">'
        f'{label}</span></div>'
        f'<div style="font-size:11px;color:{MUTED};font-family:{MONO};'
        f'margin:4px 0 0 15px;word-break:break-all">{client.base_url}</div>',
        unsafe_allow_html=True)

    st.markdown(
        f'<div style="font-size:11.5px;color:{MUTED};margin-top:26px;'
        f'padding-top:14px;border-top:1px solid {BORDER};line-height:1.6;'
        f'font-family:{BODY}">Deterministic analysis produced the facts. '
        f'AI produced only cited interpretation.</div>',
        unsafe_allow_html=True)

if page == "Projects":
    projects.render(client)
else:
    project_detail.render(client)
