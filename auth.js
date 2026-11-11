"use strict";

const EXPECTED_SERVER_VERSION = "zeni-chat-1";
const elements = {
  form: document.querySelector("#authForm"),
  nameField: document.querySelector("#nameField"),
  name: document.querySelector("#authName"),
  email: document.querySelector("#authEmail"),
  password: document.querySelector("#authPassword"),
  heading: document.querySelector("#authHeading"),
  description: document.querySelector("#authDescription"),
  submit: document.querySelector("#authSubmit"),
  switchText: document.querySelector("#authSwitchText"),
  switchButton: document.querySelector("#authSwitchButton"),
  migrationArea: document.querySelector("#migrationArea"),
  feedback: document.querySelector("#authFeedback")
};
let registering = false;

function showFeedback(message, isError = false) {
  elements.feedback.textContent = message;
  elements.feedback.classList.toggle("error", isError);
  elements.feedback.hidden = !message;
}

function setMode(isRegistering) {
  registering = isRegistering;
  elements.nameField.hidden = !registering;
  elements.name.required = registering;
  elements.password.minLength = registering ? "12" : "";
  elements.password.autocomplete = registering ? "new-password" : "current-password";
  elements.heading.textContent = registering ? "Crie sua conta" : "Entre na sua conta";
  elements.description.textContent = registering
    ? "Crie sua conta para salvar suas conversas e acompanhar seu plano."
    : "Acesse suas conversas, seu plano e continue de onde parou.";
  elements.submit.textContent = registering ? "Criar conta" : "Entrar";
  elements.switchText.textContent = registering ? "Já tem uma conta?" : "Ainda não tem uma conta?";
  elements.switchButton.textContent = registering ? "Entrar" : "Criar conta";
  showFeedback("");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const version = response.headers.get("X-ZENI-Server-Version");
  if (version !== EXPECTED_SERVER_VERSION) {
    throw new Error(
      `Backend incompatível: recebido ${version || "sem versão"}; esperado ${EXPECTED_SERVER_VERSION}. Reinicie o servidor ZENI atualizado.`
    );
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `O servidor respondeu com HTTP ${response.status}.`);
  }
  return payload;
}

elements.switchButton.addEventListener("click", () => setMode(!registering));
elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.submit.disabled = true;
  showFeedback("");
  try {
    await requestJson(registering ? "/auth/register" : "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: elements.name.value,
        email: elements.email.value,
        password: elements.password.value
      })
    });
    const next = new URLSearchParams(window.location.search).get("next");
    const safeNext = next && next.startsWith("/") && !next.startsWith("//") && !next.includes("\\")
      ? next
      : "/";
    window.location.assign(safeNext);
  } catch (error) {
    showFeedback(error.message, true);
    elements.submit.disabled = false;
  }
});

requestJson("/api/me")
  .then((account) => {
    if (account.authenticated) {
      window.location.replace("/");
      return;
    }
    elements.migrationArea.hidden = !account.auth_configured;
  })
  .catch((error) => showFeedback(`Não foi possível consultar a conta: ${error.message}`, true));
