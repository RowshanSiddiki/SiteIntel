// Project AI Agent: scoped conversations and grounded, read-only tool results.
(() => {
  const root = document.querySelector("#project-workspace");
  const page = document.querySelector("#ai-agent");
  if (!root || !page) return;

  const $ = (selector) => page.querySelector(selector);
  const token = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const base = `/api/projects/${root.dataset.projectId}/agent`;
  const historyList = $("#agent-conversations");
  const messages = $("#agent-messages");
  const form = $("#agent-form");
  const question = $("#agent-question");
  const send = $("#agent-send");
  let conversationId = null;

  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const show = (element, text) => {
    element.textContent = text || "";
    element.hidden = !text;
  };
  const api = async (path, options = {}) => {
    let response;
    try {
      response = await fetch(base + path, {
        credentials: "same-origin",
        ...options,
        headers: { "X-CSRFToken": token, ...(options.headers || {}) },
      });
    } catch {
      throw new Error("Could not reach the server. Check your connection and try again.");
    }
    let body = {};
    try { body = await response.json(); } catch { /* report a consistent request error */ }
    if (!response.ok) {
      throw new Error(body?.error?.message || "The request failed. Please try again.");
    }
    return body;
  };
  const postJson = (path, body) => api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  function renderAction(action) {
    const details = node("details", "agent-action");
    const summary = node(
      "summary",
      "",
      `${action.label || action.name || "Project data"} · ${action.status || "completed"}`,
    );
    details.append(summary);
    const result = action.result;
    if (result?.sources?.length) {
      const sourceList = node("ul", "agent-sources");
      result.sources.forEach((source) => {
        const item = node("li");
        const reference = Number.isInteger(source.number) ? `[${source.number}] ` : "";
        const label = `${reference}${source.document_name || "Document"}${source.page_number ? ` · page ${source.page_number}` : ""}`;
        if (source.view_url) {
          const link = node("a", "text-link", label);
          link.href = source.view_url;
          link.target = "_blank";
          link.rel = "noopener";
          item.append(link);
        } else {
          item.textContent = label;
        }
        if (source.excerpt) item.append(node("p", "muted", source.excerpt));
        sourceList.append(item);
      });
      details.append(sourceList);
    }
    const output = node("pre", "agent-action-output", JSON.stringify(result, null, 2));
    details.append(output);
    return details;
  }

  function renderMessage(item) {
    const article = node(`article`, `agent-message agent-message-${item.role}`);
    article.append(node("p", "agent-message-label", item.role === "user" ? "You" : "AI Agent"));
    article.append(node("div", "agent-message-content", item.content || ""));
    if (Array.isArray(item.actions) && item.actions.length) {
      const actions = node("section", "agent-actions");
      actions.append(node("h3", "", "Project data used"));
      item.actions.forEach((action) => actions.append(renderAction(action)));
      article.append(actions);
    }
    messages.append(article);
  }

  function renderMessages(items) {
    messages.replaceChildren();
    if (!items.length) {
      const empty = node("div", "empty-state compact");
      empty.append(
        node("h3", "", "How can I help with this project?"),
        node("p", "", "Ask for a progress summary, report comparison, document search, saved prediction, or expense breakdown."),
      );
      messages.append(empty);
      return;
    }
    items.forEach(renderMessage);
    messages.scrollTop = messages.scrollHeight;
  }

  async function loadConversations(selectId) {
    show($("#agent-history-status"), "Loading conversations…");
    historyList.replaceChildren();
    try {
      const data = await api("/conversations");
      show($("#agent-history-status"), data.conversations.length ? "" : "No conversations yet.");
      data.conversations.forEach((item) => {
        const button = node("button", "agent-conversation", item.title);
        button.type = "button";
        button.dataset.conversationId = String(item.id);
        button.setAttribute("aria-current", String(item.id === selectId));
        button.addEventListener("click", () => loadConversation(item.id));
        historyList.append(button);
      });
      if (selectId) await loadConversation(selectId);
      else if (data.conversations.length && conversationId === null) {
        await loadConversation(data.conversations[0].id);
      }
    } catch (error) {
      show($("#agent-history-status"), error.message);
    }
  }

  async function loadConversation(id) {
    show($("#agent-error"), "");
    show($("#agent-status"), "Loading conversation…");
    try {
      const data = await api(`/conversations/${id}`);
      conversationId = data.conversation.id;
      renderMessages(data.messages || []);
      historyList.querySelectorAll(".agent-conversation").forEach((button) => {
        button.setAttribute(
          "aria-current",
          String(button.dataset.conversationId === String(data.conversation.id)),
        );
      });
    } catch (error) {
      show($("#agent-error"), error.message);
    } finally {
      show($("#agent-status"), "");
    }
  }

  $("#agent-new").addEventListener("click", () => {
    conversationId = null;
    historyList.querySelectorAll(".agent-conversation").forEach((button) => button.setAttribute("aria-current", "false"));
    renderMessages([]);
    question.focus();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const content = question.value.trim();
    if (!content) return;
    send.disabled = true;
    question.disabled = true;
    show($("#agent-error"), "");
    show($("#agent-status"), "Checking project records and preparing a response…");
    try {
      const data = await postJson("/chat", {
        message: content,
        ...(conversationId ? { conversation_id: conversationId } : {}),
      });
      conversationId = data.conversation.id;
      const current = Array.from(messages.querySelectorAll(".agent-message")).map((item) => ({
        role: item.classList.contains("agent-message-user") ? "user" : "assistant",
        content: item.querySelector(".agent-message-content")?.textContent || "",
      }));
      current.push({ role: "user", content });
      current.push(data.message);
      renderMessages(current);
      question.value = "";
      await loadConversations(conversationId);
    } catch (error) {
      show($("#agent-error"), error.message);
    } finally {
      send.disabled = false;
      question.disabled = false;
      show($("#agent-status"), "");
      question.focus();
    }
  });

  loadConversations(null);
})();
