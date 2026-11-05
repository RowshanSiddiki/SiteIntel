from typing import Any

import streamlit as st

from frontend.components.common import render_brand
from frontend.components.navigation import render_navigation


def render_sidebar(
    *,
    authenticated_user: dict[str, Any] | None,
    current_page: str,
) -> tuple[str | None, bool]:
    with st.sidebar:
        render_brand(compact=True)
        st.caption("Project intelligence for the built environment")
        st.divider()
        if authenticated_user is None:
            st.markdown("### Account")
            sign_in = st.button("Sign in", use_container_width=True)
            create_account = st.button(
                "Create account", use_container_width=True, type="primary"
            )
            if sign_in:
                current_page = "login"
            elif create_account:
                current_page = "register"
            return current_page, False

        full_name = authenticated_user.get("full_name", "SiteIntel member")
        st.markdown(f"**{full_name}**")
        st.caption(authenticated_user.get("email", ""))
        st.divider()
        selected_page = render_navigation(current_page)
        st.divider()
        logout = st.button("↪  Sign out", use_container_width=True)
        return selected_page, logout
