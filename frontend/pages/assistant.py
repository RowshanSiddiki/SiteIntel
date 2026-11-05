import json
from typing import Any

import streamlit as st

from frontend.api_client import APIClient, APIError
from frontend.components.common import show_api_error


def _show_message(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message.get("content", ""))
        result = message.get("result")
        if isinstance(result, dict):
            with st.expander("Structured tool result", expanded=True):
                st.json(result)
        required_inputs = message.get("required_inputs")
        if isinstance(required_inputs, list) and required_inputs:
            st.info(
                "This tool needs explicit input values: "
                + ", ".join(str(item) for item in required_inputs)
            )


def _send_question(api: APIClient, question: str, inputs: dict[str, Any] | None = None):
    response = api.assistant_chat(question=question, inputs=inputs)
    if not isinstance(response.get("status"), str):
        raise APIError("The assistant returned an invalid response.")
    return response


def render(api: APIClient) -> None:
    st.markdown('<div class="siteintel-eyebrow">SiteIntel tools</div>', unsafe_allow_html=True)
    st.title("AI Assistant")
    st.caption(
        "Ask about workforce, cement demand, supply chain, construction cost, "
        "civil risk, or construction estimates."
    )

    messages = st.session_state.setdefault("assistant_messages", [])
    for message in messages:
        _show_message(message)

    prompt = st.chat_input("What would you like to understand?")
    if prompt:
        for previous_message in reversed(messages):
            if previous_message["role"] == "assistant":
                previous_message.pop("required_inputs", None)
                break
        messages.append({"role": "user", "content": prompt})
        _show_message(messages[-1])
        with st.chat_message("assistant"):
            with st.spinner("Checking the appropriate SiteIntel tool..."):
                try:
                    response = _send_question(api, prompt)
                except APIError as error:
                    show_api_error(error)
                    return
                content = response.get("response")
                assistant_message = {
                    "role": "assistant",
                    "content": (
                        content
                        if isinstance(content, str)
                        else "The assistant returned no explanation."
                    ),
                }
                result = response.get("result")
                if isinstance(result, dict):
                    assistant_message["result"] = result
                required = response.get("required_inputs")
                if isinstance(required, list):
                    assistant_message["required_inputs"] = required
                messages.append(assistant_message)
                st.markdown(assistant_message["content"])
                if isinstance(result, dict):
                    with st.expander("Structured tool result", expanded=True):
                        st.json(result)
                if isinstance(required, list) and required:
                    st.info(
                        "This tool needs explicit input values: "
                        + ", ".join(str(item) for item in required)
                    )

    pending = next(
        (
            message
            for message in reversed(messages)
            if message["role"] == "assistant"
            and message.get("required_inputs")
        ),
        None,
    )
    if pending is None:
        return

    required_fields = pending["required_inputs"]
    st.markdown("### Provide prediction inputs")
    st.caption(
        "Enter every listed feature as a JSON object. Values are not inferred or "
        "filled in automatically."
    )
    st.code(", ".join(str(field) for field in required_fields), language="text")
    with st.form("assistant_prediction_inputs"):
        raw_inputs = st.text_area(
            "Feature values (JSON)",
            value="{}",
            height=130,
            placeholder='{"feature_name": 12.5}',
        )
        submitted = st.form_submit_button("Run prediction", type="primary")
    if submitted:
        try:
            parsed_inputs = json.loads(raw_inputs)
        except json.JSONDecodeError:
            st.error("Enter valid JSON for the prediction inputs.")
            return
        if not isinstance(parsed_inputs, dict):
            st.error("Prediction inputs must be a JSON object.")
            return

        original_question = next(
            (
                message["content"]
                for message in reversed(messages[:-1])
                if message["role"] == "user"
            ),
            "",
        )
        with st.spinner("Running the prediction..."):
            try:
                response = _send_question(api, original_question, parsed_inputs)
            except APIError as error:
                show_api_error(error)
                return
        if response.get("status") == "succeeded":
            pending.pop("required_inputs", None)
        answer = response.get("response")
        result = response.get("result")
        messages.append(
            {
                "role": "assistant",
                "content": answer if isinstance(answer, str) else "Prediction completed.",
                **({"result": result} if isinstance(result, dict) else {}),
            }
        )
        st.rerun()
