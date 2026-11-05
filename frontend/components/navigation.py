import streamlit as st


PAGES = {
    "dashboard": "Overview",
    "assistant": "AI Assistant",
}


def render_navigation(current_page: str) -> str:
    labels = list(PAGES.values())
    page_keys = list(PAGES)
    selected_index = page_keys.index(current_page) if current_page in PAGES else 0
    selected_label = st.sidebar.radio(
        "Workspace",
        labels,
        index=selected_index,
        format_func=lambda label: f"{'▦' if label == 'Overview' else '✦'}  {label}",
        label_visibility="collapsed",
    )
    return page_keys[labels.index(selected_label)]
