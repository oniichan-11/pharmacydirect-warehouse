"""
Visual theme — Pharmacy Direct brand chrome, shared with the walk-in demand tracker.

Palette is taken from the Pharmacy Direct logo (navy #01528A, amber #F6A925);
typography pairs Inter (body) with Sora (display headings, big figures).
Keep these values in step with walk-in-demand-tracker/config.py so the two
apps look like one product.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

import streamlit as st

LOGO_PATH = Path(__file__).parent / "assets" / "logo.png"

BRAND_NAVY = "#01528A"
BRAND_NAVY_DARK = "#013A62"
BRAND_NAVY_TINT = "#E8F0F7"
BRAND_AMBER = "#F6A925"
BRAND_AMBER_TINT = "#FDF3E0"
INK = "#16202B"
INK_MUTED = "#5B6B7C"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#F5F8FB"
BORDER = "#DEE6EE"

# Row tints for expiry status in tables (soft, so text stays readable)
STATUS_COLORS = {"Expired": "background-color:#F8D7D3", "≤ 3 months": "background-color:#FCE3B6",
                 "≤ 6 months": f"background-color:{BRAND_AMBER_TINT}"}


def _css() -> str:
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Sora:wght@400;500;600;700&display=swap');

html, body, [class*="st-"], button, input, textarea, select {{
    font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
}}
/* keep Streamlit's icon font, or icons print as raw text like "keyboard_arrow_right" */
[data-testid="stIconMaterial"] {{
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined' !important;
}}

.stApp {{ background: {SURFACE}; }}
.block-container {{ padding-top: 2.4rem; padding-bottom: 4rem; max-width: 1280px; }}

h1, h2, h3, h4,
[data-testid="stMarkdownContainer"] h1, [data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3, [data-testid="stMarkdownContainer"] h4,
[data-testid="stHeading"] {{
    font-family: 'Sora', 'Inter', system-ui, sans-serif !important;
    color: {INK}; letter-spacing: -0.02em; font-weight: 600;
}}
[data-testid="stHeading"] h3 {{ font-size: 1.18rem; }}
p, span, label, li {{ font-weight: 400; }}

/* ---- Brand hero header ------------------------------------------------- */
.pd-header {{
    position: relative; overflow: hidden;
    display: flex; align-items: center; gap: 1.1rem;
    background: linear-gradient(100deg, {BRAND_NAVY} 0%, {BRAND_NAVY_DARK} 100%);
    border-radius: 20px; padding: 1.5rem 1.9rem; margin-bottom: 1.8rem;
    box-shadow: 0 10px 30px -12px rgba(1,82,138,0.45);
}}
.pd-header::after {{
    content: ""; position: absolute; top: -60%; right: -8%;
    width: 320px; height: 320px; border-radius: 50%;
    background: radial-gradient(circle, {BRAND_AMBER}33 0%, transparent 70%);
}}
.pd-header .pd-bar {{
    width: 6px; align-self: stretch; border-radius: 6px; background: {BRAND_AMBER}; flex: none;
}}
.pd-header h1 {{
    font-family: 'Sora', sans-serif; color: #fff !important; font-size: 1.7rem; font-weight: 600;
    margin: 0; padding: 0; line-height: 1.15; letter-spacing: -0.02em;
}}
.pd-header p {{
    color: rgba(255,255,255,0.82); font-size: 0.92rem; margin: 0.35rem 0 0 0; max-width: 70ch;
}}

/* ---- Sidebar ---------------------------------------------------------- */
section[data-testid="stSidebar"] {{ background: #fff; border-right: 1px solid {BORDER}; }}
section[data-testid="stSidebar"] .pd-side-meta {{
    background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: 14px;
    padding: 0.85rem 1rem; margin-bottom: 0.6rem;
}}
section[data-testid="stSidebar"] .pd-side-meta .k {{
    font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.07em;
    color: {INK_MUTED}; font-weight: 600;
}}
section[data-testid="stSidebar"] .pd-side-meta .v {{
    font-size: 0.95rem; color: {INK}; font-weight: 600; margin-bottom: 0.5rem;
}}
section[data-testid="stSidebar"] .pd-side-meta .v:last-child {{ margin-bottom: 0; }}

/* Sidebar page menu (a radio) styled as the tracker's pill nav */
section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 0.15rem; }}
section[data-testid="stSidebar"] div[role="radiogroup"] label {{
    border-radius: 999px; padding: 0.42rem 0.9rem; margin: 0; width: 100%;
    transition: background 0.12s ease;
}}
section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{ background: {SURFACE_ALT}; }}
/* hide the radio dot (the element just before the label text) */
section[data-testid="stSidebar"] div[role="radiogroup"] label div:has(+ [data-testid="stMarkdownContainer"]) {{
    display: none;
}}
section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {{
    background: {BRAND_NAVY_TINT};
}}
section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p {{
    color: {BRAND_NAVY} !important; font-weight: 600;
}}

/* ---- Stat tiles ------------------------------------------------------- */
div[data-testid="stMetric"] {{
    background: #fff; border: 1px solid {BORDER}; border-radius: 18px;
    padding: 1.15rem 1.25rem 1rem; box-shadow: 0 6px 20px -14px rgba(22,32,43,0.4);
    transition: box-shadow 0.15s ease, transform 0.15s ease;
}}
div[data-testid="stMetric"]:hover {{
    box-shadow: 0 10px 26px -14px rgba(1,82,138,0.5); transform: translateY(-2px);
}}
div[data-testid="stMetric"]::before {{
    content: ""; display: block; width: 26px; height: 3px; border-radius: 3px;
    background: {BRAND_NAVY}; margin-bottom: 0.7rem;
}}
div[data-testid="stMetric"] label p {{
    font-size: 0.74rem !important; color: {INK_MUTED} !important;
    font-weight: 600 !important; letter-spacing: 0.02em; text-transform: uppercase;
    white-space: normal !important; overflow: visible !important; line-height: 1.3;
}}
div[data-testid="stMetricLabel"], div[data-testid="stMetricLabel"] * {{
    overflow: visible !important; text-overflow: clip !important; white-space: normal !important;
}}
div[data-testid="stMetricValue"] {{
    font-family: 'Sora', sans-serif !important; font-size: 2rem !important;
    font-weight: 600 !important; color: {INK} !important; line-height: 1.1;
}}

/* ---- Panels, forms & tables -------------------------------------------- */
div[data-testid="stExpander"], div[data-testid="stForm"] {{
    border: 1px solid {BORDER}; border-radius: 16px; background: #fff;
    box-shadow: 0 4px 16px -14px rgba(22,32,43,0.35);
}}
div[data-testid="stExpander"] summary {{ font-weight: 500; }}
div[data-testid="stDataFrame"], div[data-testid="stTable"] {{
    border: 1px solid {BORDER}; border-radius: 14px; overflow: hidden;
}}

/* ---- Buttons — the pill ----------------------------------------------- */
.stButton button, .stDownloadButton button, div[data-testid="stFormSubmitButton"] button {{
    border-radius: 999px !important; padding: 0.6rem 1.5rem !important;
    font-weight: 500 !important; letter-spacing: 0.01em; transition: all 0.15s ease;
}}
button[kind="primary"], button[kind="primaryFormSubmit"] {{
    background: {BRAND_NAVY} !important; color: #fff !important;
    border: 1.6px solid {BRAND_NAVY} !important; box-shadow: 0 6px 18px -8px rgba(1,82,138,0.55);
}}
button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover {{
    background: {BRAND_NAVY_DARK} !important; border-color: {BRAND_NAVY_DARK} !important;
    transform: translateY(-1px); box-shadow: 0 10px 22px -8px rgba(1,82,138,0.6);
}}
button[kind="secondary"], button[kind="secondaryFormSubmit"] {{
    background: #fff !important; color: {BRAND_NAVY} !important; border: 1.6px solid {BORDER} !important;
}}
button[kind="secondary"]:hover, button[kind="secondaryFormSubmit"]:hover {{
    border-color: {BRAND_NAVY} !important; color: {BRAND_NAVY_DARK} !important;
}}

div[data-baseweb="input"], div[data-baseweb="select"] > div, .stTextArea textarea {{
    border-radius: 11px !important;
}}

/* ---- Tabs & alerts ------------------------------------------------------ */
button[data-baseweb="tab"][aria-selected="true"] p {{ color: {BRAND_NAVY}; font-weight: 600; }}
div[data-baseweb="tab-highlight"] {{ background-color: {BRAND_AMBER}; }}
div[data-testid="stAlert"] {{ border-radius: 14px; }}

.pd-eyebrow {{
    display: inline-block; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em;
    color: {BRAND_NAVY}; font-weight: 700; margin: 0 0 0.4rem 0;
    padding: 0.15rem 0.6rem; border-radius: 999px; background: {BRAND_NAVY_TINT};
}}
</style>
"""


def apply() -> None:
    """Inject the brand CSS. Call once, right after st.set_page_config."""
    st.markdown(_css(), unsafe_allow_html=True)


def header(title: str, subtitle: str = "") -> None:
    """Brand hero header — navy block, amber keyline + glow, Sora title."""
    sub = f"<p>{escape(subtitle)}</p>" if subtitle else ""
    st.markdown(f'<div class="pd-header"><div class="pd-bar"></div><div><h1>{escape(title)}</h1>{sub}</div></div>',
                unsafe_allow_html=True)


def side_meta(pairs: dict[str, str]) -> None:
    """Small key/value card in the sidebar (e.g. signed-in user)."""
    rows = "".join(f'<div class="k">{escape(k)}</div><div class="v">{escape(v)}</div>' for k, v in pairs.items())
    st.markdown(f'<div class="pd-side-meta">{rows}</div>', unsafe_allow_html=True)


def logo_exists() -> bool:
    return LOGO_PATH.exists()
