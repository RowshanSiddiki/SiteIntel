import streamlit as st

from frontend.api_client import APIClient, APIError
from frontend.components.common import apply_styles, render_brand
from frontend.components.sidebar import render_sidebar
from frontend.pages import assistant, dashboard, login, register


def _api_client() -> APIClient:
    client = st.session_state.get("siteintel_api_client")
    if not isinstance(client, APIClient):
        client = APIClient()
        st.session_state["siteintel_api_client"] = client
    return client


def _clear_authentication(api: APIClient) -> None:
    api.clear_session()
    st.session_state.pop("authenticated_user", None)
    st.session_state.pop("assistant_messages", None)
    st.session_state["current_page"] = "login"


def main() -> None:
    st.set_page_config(
        page_title="SiteIntel | Construction Intelligence",
        page_icon="🏗️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_styles()
    api = _api_client()
    user = st.session_state.get("authenticated_user")
    if isinstance(user, dict):
        try:
            user = api.current_user()
            st.session_state["authenticated_user"] = user
        except APIError as error:
            if error.status_code == 401:
                _clear_authentication(api)
                user = None
            else:
                st.warning(
                    "The backend session could not be refreshed. "
                    "Check the connection before making protected requests."
                )

    current_page = st.session_state.get(
        "current_page", "dashboard" if isinstance(user, dict) else "login"
    )
    selected_page, logout_clicked = render_sidebar(
        authenticated_user=user if isinstance(user, dict) else None,
        current_page=current_page,
    )
    if selected_page is not None:
        current_page = selected_page
        st.session_state["current_page"] = selected_page

    if logout_clicked:
        logout_error: APIError | None = None
        try:
            api.logout()
        except APIError as error:
            logout_error = error
        _clear_authentication(api)
        if logout_error is not None:
            st.warning(
                "The local application session was cleared, but the backend "
                f"could not confirm sign out: {logout_error}"
            )
        st.rerun()

    if not isinstance(user, dict):
        render_brand()
        st.caption("A secure workspace for construction project intelligence.")
        if current_page == "register":
            register.render(api)
        else:
            login.render(api)
        return

    if current_page == "assistant":
        assistant.render(api)
    else:
        dashboard.render(api, user)


if __name__ == "__main__":
    main()
