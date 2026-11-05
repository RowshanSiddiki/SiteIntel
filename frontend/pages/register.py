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
              <div class="siteintel-eyebrow">Start your workspace</div>
              <h2>Make better-informed project decisions.</h2>
              <p class="siteintel-muted">Create an account to access SiteIntel's construction-focused dashboard and assistant.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with form_column:
        st.markdown("## Create account")
        st.caption("Your account is securely managed by the SiteIntel backend.")
        with st.form("register_form", clear_on_submit=True):
            full_name = st.text_input("Full name", autocomplete="name")
            email = st.text_input("Email", autocomplete="email")
            password = st.text_input(
                "Password", type="password", autocomplete="new-password"
            )
            confirmation = st.text_input(
                "Confirm password", type="password", autocomplete="new-password"
            )
            submitted = st.form_submit_button(
                "Create account", type="primary", use_container_width=True
            )
        if submitted:
            try:
                payload = api.register(
                    full_name=full_name,
                    email=email,
                    password=password,
                    password_confirmation=confirmation,
                )
                user: Any = payload.get("user")
                if not isinstance(user, dict):
                    raise APIError("The backend did not confirm account creation.")
                st.success("Account created. Sign in to open your workspace.")
                st.session_state["current_page"] = "login"
                st.rerun()
            except APIError as error:
                show_api_error(error)
    with right:
        st.empty()
