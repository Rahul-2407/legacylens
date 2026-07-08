"""LegacyLens design system - the single source of visual truth.

Direction: an assessment ledger. A beige paper canvas (#EFEAE1), white
plates with hairline beige-tinted borders, and near-black ink spent in
exactly three places: the readiness verdict plate, primary actions, and
section markers. Color otherwise belongs to content (severity), never to
chrome. Type: Space Grotesk for display, Inter for body, IBM Plex Mono
for anything machine-shaped (paths, IDs, rule codes). No emojis anywhere.
"""

# ---------------------------------------------------------------- palette
CANVAS = "#EFEAE1"      # beige paper
CARD = "#FFFFFF"        # white plate
BORDER = "#DDD6C8"      # beige hairline
INK = "#141412"         # near-black, used sparingly
INK_SOFT = "#3D3A33"    # secondary text
MUTED = "#8C8577"       # captions, labels
SUBTLE = "#E7E1D4"      # recessed fills
PAPER_ON_INK = "#EFEAE1"  # beige numerals on the black plate

SEVERITY = {
    "critical": "#A03225",
    "high": "#B26424",
    "medium": "#96781C",
    "low": "#3D6478",
    "info": "#8C8577",
}
SEVERITY_BG = {
    "critical": "#F3E4E1",
    "high": "#F3E9DD",
    "medium": "#F1ECD9",
    "low": "#E2EAEF",
    "info": "#E7E1D4",
}
STATUS = {
    "completed": ("#2E5F41", "#E1EBE3"),
    "running": ("#8A6A1C", "#F1ECD9"),
    "pending": ("#6E685C", "#E7E1D4"),
    "failed": ("#A03225", "#F3E4E1"),
}

# ------------------------------------------------------------------- type
DISPLAY = "'Space Grotesk', 'Inter', sans-serif"
BODY = ("'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', "
        "Roboto, sans-serif")
MONO = "'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace"
FONT_STACK = BODY  # kept for backward compatibility with older imports


def base_css() -> str:
    """Global CSS injected once per page to override Streamlit chrome."""
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

/* ------------------------------------------------------ canvas & type */
.stApp {{ background: {CANVAS}; }}
header[data-testid="stHeader"] {{
    background: {CANVAS};            /* blend the top bar into the canvas */
    border-bottom: none;
    box-shadow: none;
}}
[data-testid="stDecoration"] {{ display: none; }}   /* gradient strip */
[data-testid="stAppDeployButton"] {{ display: none; }}
.stDeployButton {{ display: none; }}
#MainMenu {{ visibility: hidden; }}
footer {{ visibility: hidden; }}
.block-container {{ padding-top: 3.6rem; padding-bottom: 4rem;
                    max-width: 1360px; }}
html, body, [class*="css"] {{
    font-family: {BODY}; color: {INK_SOFT};
    -webkit-font-smoothing: antialiased;
}}
/* Streamlit's icons (sidebar collapse, expander chevrons) are font
   ligatures - they must keep the Material Symbols font or the raw
   text like "keyboard_double_arrow_left" shows instead of the glyph. */
[data-testid="stIconMaterial"],
.material-symbols-rounded,
.material-symbols-outlined,
span[class*="material-symbols"] {{
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined'
        !important;
}}
h1, h2, h3 {{ font-family: {DISPLAY}; color: {INK};
              letter-spacing: -0.02em; font-weight: 600; }}
code {{ font-family: {MONO}; background: {SUBTLE}; color: {INK_SOFT};
        padding: 1px 6px; border-radius: 6px; font-size: 0.85em; }}

/* Sidebar collapse/expand controls: these icons are font ligatures and
   show raw text like "keyboard_double_arrow_left" when the icon font is
   blocked (Brave shields, offline). Hide the text and draw the arrows
   in plain CSS so they can never break. */
[data-testid="stSidebarCollapseButton"] span[data-testid="stIconMaterial"],
[data-testid="stSidebarCollapseButton"] button span,
[data-testid="stSidebarCollapsedControl"] span[data-testid="stIconMaterial"],
[data-testid="stSidebarCollapsedControl"] button span,
[data-testid="stExpandSidebarButton"] span[data-testid="stIconMaterial"] {{
    font-size: 0 !important;
    line-height: 0 !important;
    letter-spacing: 0 !important;
}}
[data-testid="stSidebarCollapseButton"]
    span[data-testid="stIconMaterial"]::before,
[data-testid="stSidebarCollapseButton"] button span::before {{
    content: "\\00AB";                 /* double left angle */
    font-family: {BODY};
    font-size: 20px; line-height: 1; color: {INK};
}}
[data-testid="stSidebarCollapsedControl"]
    span[data-testid="stIconMaterial"]::before,
[data-testid="stSidebarCollapsedControl"] button span::before,
[data-testid="stExpandSidebarButton"]
    span[data-testid="stIconMaterial"]::before {{
    content: "\\00BB";                 /* double right angle */
    font-family: {BODY};
    font-size: 20px; line-height: 1; color: {INK};
}}

/* ------------------------------------------------------------ sidebar */
section[data-testid="stSidebar"] {{
    background: {CANVAS};
    border-right: 1px solid {BORDER};
}}
section[data-testid="stSidebar"]
    *:not([data-testid="stIconMaterial"]):not([class*="material-symbols"]) {{
    font-family: {BODY};
}}
section[data-testid="stSidebar"] .stRadio label {{
    padding: 9px 12px; border-radius: 10px; width: 100%;
    font-size: 14px; color: {INK_SOFT}; transition: background .12s ease;
}}
section[data-testid="stSidebar"] .stRadio label:hover {{
    background: {SUBTLE};
}}
section[data-testid="stSidebar"] .stRadio label[data-checked="true"],
section[data-testid="stSidebar"] .stRadio label:has(input:checked) {{
    background: {INK}; color: {PAPER_ON_INK};
}}
section[data-testid="stSidebar"] .stRadio label:has(input:checked) p {{
    color: {PAPER_ON_INK}; font-weight: 500;
}}
section[data-testid="stSidebar"] .stRadio [role="radiogroup"] {{ gap: 4px; }}
section[data-testid="stSidebar"] .stRadio input {{ display: none; }}
section[data-testid="stSidebar"] .stRadio div[data-testid="stMarkdownContainer"] p {{
    font-size: 14px;
}}
section[data-testid="stSidebar"] hr {{ border-color: {BORDER}; }}

/* ------------------------------------------------------------ buttons */
.stButton > button, .stDownloadButton > button {{
    border-radius: 10px; border: 1px solid {BORDER};
    background: {CARD}; color: {INK};
    font-family: {BODY}; font-weight: 500; font-size: 14px;
    padding: 0.45rem 1.1rem; box-shadow: none;
    transition: border-color .12s ease, background .12s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
    border-color: {INK}; color: {INK}; background: {CARD};
}}
.stButton > button[kind="primary"] {{
    background: {INK}; color: {PAPER_ON_INK}; border: 1px solid {INK};
}}
.stButton > button[kind="primary"]:hover {{
    background: #2A2A26; border-color: #2A2A26; color: {PAPER_ON_INK};
}}
.stButton > button:disabled {{
    background: {SUBTLE}; color: {MUTED}; border-color: {BORDER};
}}

/* ------------------------------------------------------------- inputs */
[data-testid="stFileUploaderDropzone"] {{
    background: {CARD}; border: 1px dashed {BORDER}; border-radius: 12px;
}}
[data-testid="stFileUploaderDropzone"]:hover {{ border-color: {INK}; }}
.stSelectbox [data-baseweb="select"] > div,
.stMultiSelect [data-baseweb="select"] > div {{
    background: {CARD}; border-color: {BORDER}; border-radius: 10px;
    font-family: {BODY};
}}
.stMultiSelect [data-baseweb="tag"] {{
    background: {SUBTLE}; color: {INK}; border-radius: 8px;
}}
.stCheckbox p {{ font-size: 13.5px; }}

/* ----------------------------------------------------- containers etc */
[data-testid="stExpander"] {{
    border: 1px solid {BORDER}; border-radius: 14px; background: {CARD};
}}
[data-testid="stExpander"] summary {{
    font-family: {BODY}; font-weight: 500; color: {INK};
}}
[data-testid="stVerticalBlockBorderWrapper"] {{
    border-color: {BORDER} !important; border-radius: 16px !important;
    background: {CARD};
}}
[data-testid="stDataFrame"] {{
    border-radius: 14px; overflow: hidden; border: 1px solid {BORDER};
}}

/* --------------------------------------------------------------- tabs */
.stTabs [data-baseweb="tab-list"] {{
    gap: 4px; background: {SUBTLE}; padding: 4px;
    border-radius: 12px; width: fit-content;
}}
.stTabs [data-baseweb="tab"] {{
    border-radius: 9px; padding: 6px 16px; background: transparent;
    font-family: {BODY}; font-size: 13.5px; color: {INK_SOFT};
}}
.stTabs [aria-selected="true"] {{
    background: {CARD}; color: {INK}; font-weight: 500;
    box-shadow: 0 1px 2px rgba(20,20,18,0.06);
}}
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] {{ display: none; }}

/* ------------------------------------------------------------- alerts */
[data-testid="stAlert"] {{
    border-radius: 12px; border: 1px solid {BORDER}; background: {CARD};
    font-family: {BODY};
}}

/* ---------------------------------------------------------- scrollbar */
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-thumb {{
    background: {BORDER}; border-radius: 8px;
    border: 2px solid {CANVAS};
}}
::-webkit-scrollbar-track {{ background: transparent; }}

/* -------------------------------------------------------------- focus */
:focus-visible {{ outline: 2px solid {INK}; outline-offset: 2px; }}
@media (prefers-reduced-motion: reduce) {{
    * {{ transition: none !important; animation: none !important; }}
}}
</style>
"""
