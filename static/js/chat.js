(() => {
  const messagesEl = document.getElementById("messages");
  const form = document.getElementById("chat-form");
  const promptEl = document.getElementById("prompt");
  const modelEl = document.getElementById("model-select");
  const sendBtn = document.getElementById("send-btn");
  const clearBtn = document.getElementById("clear-chat");
  const defaultModel = modelEl.dataset.default || "default";

  /** @type {{role: string, content: string}[]} */
  let history = [];

  function publicModelId(id) {
    if (!id || id === "openrouter/free") return defaultModel;
    return id;
  }

  async function loadModels() {
    try {
      const res = await fetch("/api/models");
      if (!res.ok) return;
      const data = await res.json();
      const models = Array.isArray(data.data) ? data.data : [];
      if (!models.length) return;

      const current = publicModelId(modelEl.value || defaultModel);
      modelEl.innerHTML = "";
      for (const item of models) {
        const id = publicModelId(item.id);
        if (!id) continue;
        if ([...modelEl.options].some((o) => o.value === id)) continue;
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = id;
        if (id === current) opt.selected = true;
        modelEl.appendChild(opt);
      }
      if (![...modelEl.options].some((o) => o.selected) && modelEl.options.length) {
        const preferred = [...modelEl.options].find((o) => o.value === defaultModel);
        (preferred || modelEl.options[0]).selected = true;
      }
    } catch (_) {}
  }

  function appendMessage(role, content) {
    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.textContent = content;
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }

  function setBusy(busy) {
    sendBtn.disabled = busy;
    promptEl.disabled = busy;
  }

  async function sendMessage(text) {
    history.push({ role: "user", content: text });
    appendMessage("user", text);

    const assistantEl = appendMessage("assistant", "");
    setBusy(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: modelEl.value || defaultModel,
          stream: true,
          messages: history,
        }),
      });

      if (!res.ok) {
        let detail = "Ошибка запроса";
        try {
          const data = await res.json();
          detail = data.detail || data.error || JSON.stringify(data);
        } catch (_) {}
        assistantEl.classList.add("error");
        assistantEl.textContent = String(detail);
        history.pop();
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let full = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() || "";

        for (const line of parts) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data:")) continue;
          const data = trimmed.slice(5).trim();
          if (!data || data === "[DONE]") continue;
          try {
            const json = JSON.parse(data);
            const delta = json.choices?.[0]?.delta?.content || "";
            if (delta) {
              full += delta;
              assistantEl.textContent = full;
              messagesEl.scrollTop = messagesEl.scrollHeight;
            }
          } catch (_) {}
        }
      }

      if (!full) {
        assistantEl.textContent = "(пустой ответ)";
      }
      history.push({ role: "assistant", content: full || "" });
    } catch (err) {
      assistantEl.classList.add("error");
      assistantEl.textContent = err.message || "Сеть недоступна";
      history.pop();
    } finally {
      setBusy(false);
      promptEl.focus();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = promptEl.value.trim();
    if (!text) return;
    promptEl.value = "";
    sendMessage(text);
  });

  promptEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  clearBtn.addEventListener("click", () => {
    history = [];
    messagesEl.innerHTML = "";
  });

  loadModels();
})();
