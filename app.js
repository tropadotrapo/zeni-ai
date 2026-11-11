"use strict";

const EXPECTED_SERVER_VERSION = "zeni-chat-1";
const MAX_FILE_SIZE = 200 * 1024;
const MAX_CONVERSATION_MESSAGES = 100;
const allowedExtensions = new Set(["txt", "md", "csv", "json", "html", "css", "js", "ts", "py", "java", "c", "cpp", "h", "xml", "yaml", "yml", "log"]);
const state = { conversations: [], activeId: null, pendingFiles: [], busy: false, status: null, account: null };
const elements = {
  list: document.querySelector("#conversationList"),
  welcome: document.querySelector("#welcomeScreen"),
  messages: document.querySelector("#messages"),
  input: document.querySelector("#messageInput"),
  form: document.querySelector("#composerForm"),
  send: document.querySelector("#sendButton"),
  fileInput: document.querySelector("#fileInput"),
  attachmentList: document.querySelector("#attachmentList"),
  chatScroll: document.querySelector("#chatScroll"),
  settingsDialog: document.querySelector("#settingsDialog"),
  connection: document.querySelector("#connectionIndicator"),
  connectionLabel: document.querySelector("#connectionLabel"),
  settingsStatus: document.querySelector("#settingsStatus"),
  settingsModel: document.querySelector("#settingsModel"),
  sidebar: document.querySelector("#sidebar"),
  sidebarScrim: document.querySelector("#sidebarScrim"),
  userAvatar: document.querySelector("#userAvatar"),
  avatarFallback: document.querySelector("#avatarFallback"),
  profileName: document.querySelector("#profileName"),
  profileEmail: document.querySelector("#profileEmail"),
  profilePlan: document.querySelector("#profilePlan"),
  authButton: document.querySelector("#authButton"),
  passwordMigrationForm: document.querySelector("#passwordMigrationForm"),
  migrationPassword: document.querySelector("#migrationPassword"),
  passwordMigrationFeedback: document.querySelector("#passwordMigrationFeedback"),
  authPrompt: document.querySelector("#authPrompt"),
  loginButton: document.querySelector("#loginButton"),
  settingsAccountName: document.querySelector("#settingsAccountName"),
  settingsAccountDetails: document.querySelector("#settingsAccountDetails"),
  settingsPlanStatus: document.querySelector("#settingsPlanStatus"),
  plusPlanDetails: document.querySelector("#plusPlanDetails"),
  proPlanDetails: document.querySelector("#proPlanDetails")
};

async function apiRequest(url, options = {}) {
  let response;
  try {
    response = await fetch(url, { cache: "no-store", ...options });
  } catch (cause) {
    const error = new Error(
      "Não foi possível conectar ao servidor da ZENI. Verifique se ele está em execução e tente novamente."
    );
    error.connectionError = true;
    error.cause = cause;
    throw error;
  }
  const responseVersion = response.headers.get("X-ZENI-Server-Version");
  if (responseVersion !== EXPECTED_SERVER_VERSION) {
    throw new Error(`Backend incompatível: recebido ${responseVersion || "sem versão"}; esperado ${EXPECTED_SERVER_VERSION}. Reinicie o servidor ZENI atualizado.`);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `O servidor respondeu com HTTP ${response.status}.`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function refreshConversations() {
  if (!state.account?.authenticated) {
    state.conversations = [];
    state.activeId = null;
    render();
    return;
  }
  const payload = await apiRequest("/api/conversations");
  state.conversations = Array.isArray(payload.conversations) ? payload.conversations : [];
  if (!state.conversations.some((conversation) => conversation.id === state.activeId)) {
    state.activeId = null;
  }
  render();
}

function currentConversation() {
  return state.conversations.find((conversation) => conversation.id === state.activeId) || null;
}

function renderAccount() {
  const account = state.account;
  const authenticated = Boolean(account?.authenticated);
  const user = account?.user;
  elements.profileName.textContent = user?.name || "Entre na sua conta";
  elements.profileEmail.textContent = user?.email || "Histórico privado";
  elements.profilePlan.textContent = account?.plan?.name || "ZENI Free";
  elements.authButton.textContent = authenticated ? "Sair" : "Entrar";
  elements.authButton.disabled = false;
  elements.loginButton.disabled = false;
  elements.userAvatar.hidden = !user?.picture;
  elements.avatarFallback.hidden = Boolean(user?.picture);
  if (user?.picture) {
    elements.userAvatar.src = user.picture;
    elements.userAvatar.alt = `Foto de ${user.name}`;
    elements.userAvatar.onerror = () => {
      elements.userAvatar.hidden = true;
      elements.avatarFallback.hidden = false;
    };
  } else {
    elements.avatarFallback.textContent = user?.name?.slice(0, 1).toUpperCase() || "Z";
    elements.userAvatar.removeAttribute("src");
  }
  elements.authPrompt.hidden = authenticated;
  elements.settingsAccountName.textContent = user?.name || "Conta não conectada";
  elements.settingsAccountDetails.textContent = user?.email || (
    "Entre ou crie uma conta para salvar conversas com segurança."
  );
  elements.passwordMigrationForm.hidden = !authenticated || Boolean(account?.has_password);
  elements.settingsPlanStatus.textContent = account?.plan?.name || "ZENI Free";
  const plans = account?.plans || {};
  for (const [planId, detailsElement] of [
    ["plus", elements.plusPlanDetails],
    ["pro", elements.proPlanDetails]
  ]) {
    const plan = plans[planId];
    if (!plan) continue;
    detailsElement.textContent = `${plan.price} ${plan.price_period}`;
  }
  document.querySelectorAll(".suggestion-card").forEach((button) => {
    button.disabled = false;
  });
  elements.input.disabled = false;
  elements.fileInput.disabled = false;
  document.querySelector("#attachButton").disabled = false;
}

function renderHistory() {
  elements.list.replaceChildren();
  for (const conversation of [...state.conversations].sort((a, b) => (
    new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
  ))) {
    const item = document.createElement("div");
    item.className = `conversation-item${conversation.id === state.activeId ? " active" : ""}`;
    item.tabIndex = 0;
    item.setAttribute("role", "button");
    item.setAttribute("aria-label", `Abrir conversa: ${conversation.title}`);
    const title = document.createElement("span");
    title.className = "conversation-item-title";
    title.textContent = conversation.title || "Nova conversa";
    const remove = document.createElement("button");
    remove.className = "conversation-delete";
    remove.type = "button";
    remove.title = "Apagar conversa";
    remove.setAttribute("aria-label", `Apagar conversa: ${conversation.title}`);
    remove.textContent = "×";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteConversation(conversation.id);
    });
    item.append(title, remove);
    item.addEventListener("click", () => openConversation(conversation.id));
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openConversation(conversation.id);
      }
    });
    elements.list.append(item);
  }
}

function openConversation(id) {
  state.activeId = id;
  state.pendingFiles = [];
  render();
  closeSidebar();
}

function startNewChat() {
  state.activeId = null;
  state.pendingFiles = [];
  elements.input.value = "";
  render();
  elements.input.focus();
  closeSidebar();
}

async function deleteConversation(id) {
  try {
    await apiRequest(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.conversations = state.conversations.filter((conversation) => conversation.id !== id);
    if (state.activeId === id) state.activeId = null;
    render();
  } catch (error) {
    showNotice(error.message);
  }
}

function render() {
  const conversation = currentConversation();
  const messages = conversation?.messages || [];
  elements.welcome.hidden = messages.length > 0;
  elements.authPrompt.hidden = Boolean(state.account?.authenticated);
  elements.messages.classList.toggle("visible", messages.length > 0 || state.busy);
  elements.messages.replaceChildren();
  for (const message of messages) {
    elements.messages.append(createMessageElement(message));
  }
  if (state.busy) elements.messages.append(createThinkingElement());
  renderHistory();
  renderAccount();
  renderAttachments();
  updateSendButton();
  scrollToBottom();
}

function createMessageElement(message) {
  const wrapper = document.createElement("article");
  wrapper.className = `message ${message.role}${message.error ? " error" : ""}`;
  if (message.role === "assistant") {
    const mark = document.createElement("div");
    mark.className = "assistant-mark";
    mark.innerHTML = "<span aria-hidden=\"true\">✳</span> ZENI";
    wrapper.append(mark);
  }
  const content = document.createElement("div");
  content.className = "message-content";
  content.innerHTML = renderMarkdown(message.content);
  wrapper.append(content);
  if (message.role === "assistant" && !message.error) {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const copy = document.createElement("button");
    copy.className = "copy-button";
    copy.type = "button";
    copy.innerHTML = "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><rect x=\"8\" y=\"8\" width=\"12\" height=\"12\" rx=\"2\"/><path d=\"M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3\"/></svg> Copiar";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(message.content);
        copy.textContent = "Copiado";
        window.setTimeout(() => { copy.textContent = "Copiar"; }, 1500);
      } catch (error) {
        showNotice("Não foi possível copiar. Verifique as permissões do navegador.");
        console.error("Falha ao copiar resposta.", error);
      }
    });
    actions.append(copy);
    wrapper.append(actions);
  }
  return wrapper;
}

function createThinkingElement() {
  const wrapper = document.createElement("article");
  wrapper.className = "message assistant";
  wrapper.setAttribute("aria-label", "ZENI está pensando");
  wrapper.innerHTML = "<div class=\"assistant-mark\"><span aria-hidden=\"true\">✳</span> ZENI</div><div class=\"thinking\"><span></span><span></span><span></span></div>";
  return wrapper;
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
  })[character]);
}

function renderInline(text) {
  const codeSegments = [];
  let html = escapeHtml(text).replace(/`([^`]+)`/g, (_, code) => {
    codeSegments.push(`<code>${code}</code>`);
    return `\u0000${codeSegments.length - 1}\u0000`;
  });
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, href) => {
    const safeHref = escapeHtml(href);
    return `<a href="${safeHref}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(.+?)__/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/~~(.+?)~~/g, "<del>$1</del>");
  return html.replace(/\u0000(\d+)\u0000/g, (_, index) => codeSegments[Number(index)]);
}

function renderMarkdown(markdown) {
  const lines = String(markdown).replace(/\r\n?/g, "\n").split("\n");
  const output = [];
  let paragraph = [];
  let listType = null;
  let codeLines = null;
  let quoteLines = [];
  const flushParagraph = () => {
    if (paragraph.length) output.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (listType) output.push(`</${listType}>`);
    listType = null;
  };
  const flushQuote = () => {
    if (quoteLines.length) output.push(`<blockquote>${quoteLines.map(renderInline).join("<br>")}</blockquote>`);
    quoteLines = [];
  };
  for (const line of lines) {
    if (codeLines) {
      if (/^\s*```/.test(line)) {
        const code = escapeHtml(codeLines.join("\n"));
        output.push(`<pre><code>${code}</code></pre>`);
        codeLines = null;
      } else codeLines.push(line);
      continue;
    }
    const fence = line.match(/^\s*```/);
    if (fence) {
      flushParagraph(); flushList(); flushQuote(); codeLines = [];
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (heading) {
      flushParagraph(); flushList(); flushQuote();
      const level = heading[1].length;
      output.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
    } else if (unordered || ordered) {
      flushParagraph(); flushQuote();
      const nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) { flushList(); output.push(`<${nextType}>`); listType = nextType; }
      output.push(`<li>${renderInline((unordered || ordered)[1])}</li>`);
    } else if (quote) {
      flushParagraph(); flushList(); quoteLines.push(quote[1]);
    } else if (!line.trim()) {
      flushParagraph(); flushList(); flushQuote();
    } else {
      flushList(); flushQuote(); paragraph.push(line);
    }
  }
  if (codeLines) output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  flushParagraph(); flushList(); flushQuote();
  return output.join("");
}

function renderAttachments() {
  elements.attachmentList.replaceChildren();
  for (const [index, file] of state.pendingFiles.entries()) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    const name = document.createElement("span");
    name.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remover ${file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.pendingFiles.splice(index, 1);
      renderAttachments();
    });
    chip.append(name, remove);
    elements.attachmentList.append(chip);
  }
}

function updateSendButton() {
  elements.send.disabled = !state.account || state.busy || (!elements.input.value.trim() && state.pendingFiles.length === 0);
}

function scrollToBottom() {
  requestAnimationFrame(() => { elements.chatScroll.scrollTop = elements.chatScroll.scrollHeight; });
}

function showNotice(message) {
  const notice = document.createElement("div");
  notice.className = "message error";
  const content = document.createElement("div");
  content.className = "message-content";
  content.textContent = message;
  notice.append(content);
  elements.messages.append(notice);
  elements.messages.classList.add("visible");
  scrollToBottom();
}

async function submitMessage(text = elements.input.value.trim()) {
  if (!state.account || state.busy) return;
  const authenticated = Boolean(state.account?.authenticated);
  const pendingFiles = [...state.pendingFiles];
  const fileContext = state.pendingFiles.map((file) => `\n\n--- Arquivo: ${file.name} ---\n${file.content}`).join("");
  const content = `${text}${fileContext}`.trim();
  const titleText = text || state.pendingFiles[0]?.name || "Nova conversa";
  if (!content) return;

  let conversation = currentConversation();
  if (conversation && conversation.messages.filter((message) => !message.error).length >= MAX_CONVERSATION_MESSAGES - 1) {
    state.activeId = null;
    conversation = null;
  }
  if (!conversation) {
    conversation = {
      id: authenticated ? null : `guest-${crypto.randomUUID()}`,
      title: makeTitle(titleText),
      messages: [],
      updatedAt: new Date().toISOString()
    };
    state.conversations.push(conversation);
    state.activeId = conversation.id;
  }
  conversation.messages.push({ role: "user", content });
  conversation.updatedAt = new Date().toISOString();
  if (conversation.messages.length === 1 && text) conversation.title = makeTitle(text);
  state.pendingFiles = [];
  elements.input.value = "";
  resizeInput();
  state.busy = true;
  render();

  let noticeMessage = null;
  try {
    let history = conversation.messages.slice(0, -1)
      .filter((message) => !message.error)
      .map(({ role, content }) => ({ role, content }));
    let payload;
    let restartedFullConversation = false;
    while (true) {
      try {
        payload = await apiRequest("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            conversation_id: authenticated ? conversation.id : null,
            message: content,
            history: authenticated ? undefined : history
          })
        });
        break;
      } catch (error) {
        if (!error.payload?.conversation_full || restartedFullConversation) throw error;
        conversation.messages.pop();
        conversation = {
          id: authenticated ? null : `guest-${crypto.randomUUID()}`,
          title: makeTitle(titleText),
          messages: [{ role: "user", content }],
          updatedAt: new Date().toISOString()
        };
        state.conversations.push(conversation);
        state.activeId = conversation.id;
        history = [];
        restartedFullConversation = true;
        render();
      }
    }
    if (typeof payload.reply !== "string" || !payload.reply.trim()) throw new Error("O servidor retornou uma resposta vazia ou inválida.");
    if (authenticated) {
      conversation.id = payload.conversation_id;
      conversation.title = payload.title || conversation.title;
    }
    state.activeId = conversation.id;
    conversation.updatedAt = new Date().toISOString();
    conversation.messages.push({ role: "assistant", content: payload.reply });
    if (conversation.messages.filter((message) => !message.error).length >= MAX_CONVERSATION_MESSAGES) {
      state.activeId = null;
    }
  } catch (error) {
    if (error.status === 401) {
      state.conversations = state.conversations.filter((item) => item !== conversation || item.id);
      state.activeId = null;
      state.account = null;
      await refreshAccount();
      noticeMessage = error.message;
    } else if (error.status === 503 || error.connectionError) {
      const lastMessage = conversation.messages[conversation.messages.length - 1];
      if (lastMessage?.role === "user" && lastMessage.content === content) {
        conversation.messages.pop();
      }
      if (conversation.messages.length === 0) {
        state.conversations = state.conversations.filter((item) => item !== conversation);
        state.activeId = null;
      } else {
        state.activeId = conversation.id;
      }
      state.pendingFiles = pendingFiles;
      elements.input.value = text;
      resizeInput();
      noticeMessage = error.message;
    } else {
      if (error.payload?.conversation_id) {
        conversation.id = error.payload.conversation_id;
        state.activeId = conversation.id;
      } else if (!conversation.id) {
        state.conversations = state.conversations.filter((item) => item !== conversation);
        state.activeId = null;
      }
      conversation.messages.push({ role: "assistant", content: `Não consegui obter uma resposta da IA. ${error.message}`, error: true });
    }
  } finally {
    conversation.updatedAt = new Date().toISOString();
    state.busy = false;
    render();
    if (noticeMessage) showNotice(noticeMessage);
    elements.input.focus();
  }
}

function makeTitle(text) {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > 36 ? `${normalized.slice(0, 36).trim()}…` : normalized || "Nova conversa";
}

function resizeInput() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 180)}px`;
}

async function refreshStatus() {
  try {
    const status = await apiRequest("/api/status");
    state.status = status;
    if (status.provider !== "OpenRouter" || status.version !== EXPECTED_SERVER_VERSION) {
      elements.connection.classList.remove("connected");
      elements.connection.classList.add("disconnected");
      elements.connectionLabel.textContent = "Backend incompatível";
      elements.settingsStatus.textContent = "Backend incompatível";
      elements.settingsStatus.className = "settings-status disconnected";
      elements.settingsModel.textContent = `Frontend ${EXPECTED_SERVER_VERSION}; servidor ${status.version || "sem versão"} (${status.provider || "provedor desconhecido"}).`;
      return;
    }
    elements.connection.classList.toggle("connected", status.configured);
    elements.connection.classList.toggle("disconnected", !status.configured);
    elements.connectionLabel.textContent = status.configured ? "IA conectada" : "Configure a chave de API";
    elements.settingsStatus.textContent = status.configured ? "Conectada" : "Não configurada";
    elements.settingsStatus.className = `settings-status ${status.configured ? "connected" : "disconnected"}`;
    elements.settingsModel.textContent = status.model || "Modelo definido pelo servidor";
  } catch (error) {
    state.status = null;
    elements.connection.classList.add("disconnected");
    elements.connectionLabel.textContent = "Servidor indisponível";
    elements.settingsStatus.textContent = "Servidor indisponível";
    elements.settingsStatus.className = "settings-status disconnected";
    elements.settingsModel.textContent = "Não foi possível consultar a configuração.";
    console.error("Não foi possível verificar o status do servidor.", error);
  }
}

async function refreshAccount() {
  try {
    const wasAuthenticated = Boolean(state.account?.authenticated);
    state.account = await apiRequest("/api/me");
    if (state.account.authenticated) {
      await refreshConversations();
    } else {
      if (wasAuthenticated) {
        state.conversations = [];
        state.activeId = null;
      }
      render();
    }
  } catch (error) {
    state.account = { authenticated: false, auth_configured: false, has_password: false, plans: {} };
    state.conversations = [];
    state.activeId = null;
    render();
    showNotice(`Não foi possível carregar sua conta: ${error.message}`);
    console.error("Não foi possível carregar os dados da conta.", error);
  }
}

function closeSidebar() {
  elements.sidebar.classList.remove("open");
  elements.sidebarScrim.classList.remove("open");
}

document.querySelector("#newChatButton").addEventListener("click", startNewChat);
elements.authButton.addEventListener("click", async () => {
  if (state.account?.authenticated) {
    try {
      const response = await fetch("/auth/logout", { method: "POST", cache: "no-store" });
      if (!response.ok) throw new Error(`O servidor respondeu com HTTP ${response.status}.`);
      window.location.assign("/");
    } catch (error) {
      showNotice(`Não foi possível encerrar a sessão: ${error.message}`);
    }
    return;
  }
  window.location.assign("/login");
});
elements.loginButton.addEventListener("click", () => window.location.assign("/login"));
elements.passwordMigrationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = elements.passwordMigrationForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  elements.passwordMigrationFeedback.textContent = "";
  elements.passwordMigrationFeedback.classList.remove("error");
  try {
    await apiRequest("/auth/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: elements.migrationPassword.value })
    });
    state.account.has_password = true;
    elements.migrationPassword.value = "";
    renderAccount();
    showNotice("Senha definida. Agora você também pode entrar com seu e-mail e senha.");
  } catch (error) {
    elements.passwordMigrationFeedback.textContent = error.message;
    elements.passwordMigrationFeedback.classList.add("error");
  } finally {
    submitButton.disabled = false;
  }
});
document.querySelector("#settingsButton").addEventListener("click", () => elements.settingsDialog.showModal());
document.querySelector("#topSettingsButton").addEventListener("click", () => elements.settingsDialog.showModal());
document.querySelector("#closeSettingsButton").addEventListener("click", () => elements.settingsDialog.close());
document.querySelector("#mobileMenuButton").addEventListener("click", () => {
  elements.sidebar.classList.add("open");
  elements.sidebarScrim.classList.add("open");
});
elements.sidebarScrim.addEventListener("click", closeSidebar);
document.querySelector("#attachButton").addEventListener("click", () => elements.fileInput.click());
elements.fileInput.addEventListener("change", async () => {
  for (const file of elements.fileInput.files) {
    const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    if (!allowedExtensions.has(extension)) {
      showNotice(`${file.name}: este formato não é compatível. Anexe um arquivo de texto ou código.`);
      continue;
    }
    if (file.size > MAX_FILE_SIZE) {
      showNotice(`${file.name}: o arquivo excede o limite de 200 KB.`);
      continue;
    }
    try {
      state.pendingFiles.push({ name: file.name, content: await file.text() });
    } catch (error) {
      showNotice(`Não foi possível ler ${file.name}.`);
      console.error("Falha ao ler anexo.", error);
    }
  }
  elements.fileInput.value = "";
  renderAttachments();
  updateSendButton();
});
elements.input.addEventListener("input", () => { resizeInput(); updateSendButton(); });
elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements.form.requestSubmit();
  }
});
elements.form.addEventListener("submit", (event) => { event.preventDefault(); submitMessage(); });
document.querySelectorAll(".suggestion-card").forEach((button) => {
  button.addEventListener("click", () => submitMessage(button.dataset.prompt));
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    startNewChat();
  }
});

render();
refreshStatus();
const authError = new URLSearchParams(window.location.search).get("auth_error");
refreshAccount().then(() => {
  if (!authError) return;
  const messages = {
    google_not_configured: "O login Google ainda não está configurado neste servidor.",
    google_start_failed: "Não foi possível iniciar o login Google. Verifique o banco de dados e tente novamente.",
    google_login_cancelled: "O login Google foi cancelado.",
    oauth_state_invalid: "A tentativa de login expirou ou não pôde ser validada. Tente novamente.",
    google_auth_failed: "O Google não conseguiu validar o login. Verifique a configuração OAuth e tente novamente.",
    account_storage_failed: "Não foi possível salvar a sessão da conta no banco de dados."
  };
  showNotice(messages[authError] || "Não foi possível entrar na conta Google.");
  window.history.replaceState({}, "", window.location.pathname);
});
