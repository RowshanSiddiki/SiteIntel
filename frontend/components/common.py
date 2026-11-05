import streamlit as st

from frontend.api_client import APIError


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --siteintel-ink: #172b3a;
            --siteintel-muted: #607181;
            --siteintel-teal: #087f8c;
            --siteintel-pale: #eef6f5;
        }
        .stApp {
            background: linear-gradient(160deg, #f8fbfc 0%, #f3f7f8 58%, #edf4f4 100%);
            color: var(--siteintel-ink);
        }
        h1, h2, h3 { color: var(--siteintel-ink); letter-spacing: -0.025em; }
        [data-testid="stSidebar"] {
            background: #102a3a;
            border-right: 1px solid rgba(255,255,255,.08);
        }
        [data-testid="stSidebar"] * { color: #edf6f7; }
        [data-testid="stSidebar"] [data-testid="stRadio"] label {
            border-radius: 10px;
            padding: .35rem .5rem;
        }
        .siteintel-eyebrow {
            color: var(--siteintel-teal);
            font-size: .76rem;
            font-weight: 700;
            letter-spacing: .14em;
            text-transform: uppercase;
        }
        .siteintel-card {
            background: rgba(255,255,255,.92);
            border: 1px solid #e1eaec;
            border-radius: 16px;
            box-shadow: 0 8px 26px rgba(20, 52, 65, .05);
            padding: 1.15rem 1.25rem;
        }
        .siteintel-muted { color: var(--siteintel-muted); }
        div.stButton > button[kind="primary"] {
            background: var(--siteintel-teal);
            border-color: var(--siteintel-teal);
        }
        @media (max-width: 700px) {
            .block-container { padding-top: 1.2rem; padding-left: 1rem; padding-right: 1rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_brand(*, compact: bool = False) -> None:
    if compact:
        st.markdown("### 🏗️ SiteIntel")
    else:
        st.markdown(
            '<div class="siteintel-eyebrow">Construction intelligence</div>'
            "<h1>🏗️ SiteIntel</h1>",
            unsafe_allow_html=True,
        )


def show_api_error(error: Exception) -> None:
    st.error(str(error))
    if isinstance(error, APIError) and isinstance(error.details, dict):
        for field, message in error.details.items():
            if isinstance(field, str) and isinstance(message, str):
                label = field.replace("_", " ").strip().title()
                st.error(f"{label}: {message}")


def display_stats(stats: dict[str, object]) -> None:
    if not stats:
        st.info("No project statistics have been reported by the backend yet.")
        return

    items = list(stats.items())[:8]
    columns = st.columns(min(len(items), 4))
    for index, (name, value) in enumerate(items):
        with columns[index % len(columns)]:
            label = str(name).replace("_", " ").strip().title()
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                st.metric(label=label, value=value)
            else:
                st.markdown(f"**{label}**")
                st.json(value, expanded=False)
