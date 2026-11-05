import streamlit as st

from frontend.api_client import APIClient, APIError
from frontend.components.common import display_stats, show_api_error


def render(api: APIClient, user: dict[str, object]) -> None:
    st.markdown('<div class="siteintel-eyebrow">Your workspace</div>', unsafe_allow_html=True)
    st.title(f"Good to see you, {user.get('full_name', 'there')}")
    st.caption("A focused overview for your construction intelligence workflow.")

    st.markdown("### Project snapshot")
    try:
        stats = api.dashboard_stats()
    except APIError as error:
        if error.status_code == 404:
            st.info(
                "Project statistics are not available yet. They will appear here "
                "when a dashboard statistics endpoint is provided by the backend."
            )
        else:
            show_api_error(error)
    else:
        display_stats(stats)

    st.markdown("---")
    left, right = st.columns(2)
    with left:
        st.markdown(
            """
            <div class="siteintel-card">
              <div class="siteintel-eyebrow">Explore</div>
              <h3>Ask the SiteIntel assistant</h3>
              <p class="siteintel-muted">Get help from approved prediction tools and construction calculations.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            """
            <div class="siteintel-card">
              <div class="siteintel-eyebrow">Connected securely</div>
              <h3>Backend-managed account</h3>
              <p class="siteintel-muted">Your account and protected requests are validated by the Flask service.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
