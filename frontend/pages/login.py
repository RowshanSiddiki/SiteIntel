from typing import Any

import streamlit as st

from frontend.api_client import APIClient, APIError
from frontend.components.common import show_api_error


def render(api: APIClient) -> None:
    left, form_column, right = st.columns([1, 1.15, 1])
    with left:
        st.markdown(
            """
            <div class="siteintel-card">
              <div class="siteintel-eyebrow">Plan with clarity</div>
              <h2>One view of your construction work.</h2>
              <p class="siteintel-muted">Bring project intelligence and AI-assisted insights into one workspace.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with form_column:
        st.markdown("## Sign in")
        st.caption("Use your SiteIntel account to continue.")
        with st.form("login_form", clear_on_submit=True):
            email = st.text_input("Email", autocomplete="email")
            password = st.text_input(
                "Password", type="password", autocomplete="current-password"
            )
            submitted = st.form_submit_button(
                "Sign in", type="primary", use_container_width=True
            )
        if submitted:
            try:
                payload = api.login(email=email, password=password)
                user: Any = payload.get("user")
                if not isinstance(user, dict):
                    raise APIError("The backend did not return an account profile.")
                st.session_state["authenticated_user"] = user
                st.session_state["current_page"] = "dashboard"
                st.session_state.pop("login_error", None)
                st.rerun()
            except APIError as error:
                show_api_error(error)
    with right:
        st.empty()
    st.caption("New to SiteIntel? Choose **Create account** in the sidebar.")
