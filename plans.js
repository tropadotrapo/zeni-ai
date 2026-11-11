"use strict";

const EXPECTED_SERVER_VERSION = "zeni-chat-1";
const state = { account: null };
const accountStatus = document.querySelector("#planAccountStatus");
const feedback = document.querySelector("#plansFeedback");
const buttons = [...document.querySelectorAll("[data-plan-action]")];

async function apiRequest(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const version = response.headers.get("X-ZENI-Server-Version");
  if (version !== EXPECTED_SERVER_VERSION) {
    throw new Error(`Backend incompatível: recebido ${version || "sem versão"}; esperado ${EXPECTED_SERVER_VERSION}. Reinicie o servidor ZENI atualizado.`);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `O servidor respondeu com HTTP ${response.status}.`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function showFeedback(message, isError = false) {
  feedback.textContent = message;
  feedback.classList.toggle("error", isError);
  feedback.hidden = false;
}

function updateButtonsForAccount(account) {
  for (const button of buttons) {
    const planId = button.dataset.planAction;
    const current = account.authenticated && planId === account.plan?.id;
    button.closest("[data-plan-card]").classList.toggle("current-plan", current);
    if (current) {
      button.disabled = true;
      button.textContent = "Seu plano atual";
    } else if (planId === "free") {
      button.disabled = false;
      button.textContent = "Usar gratuitamente";
    } else {
      button.disabled = true;
      button.textContent = "Indisponível";
    }
  }
}

async function loadAccount() {
  try {
    state.account = await apiRequest("/api/me");
    if (state.account.authenticated) {
      accountStatus.textContent = `Conectado como ${state.account.user.name} · plano atual: ${state.account.plan.name}`;
    } else {
      accountStatus.textContent = "Use a ZENI gratuitamente. Assinaturas pagas estão indisponíveis no momento.";
    }
    updateButtonsForAccount(state.account);
  } catch (error) {
    accountStatus.textContent = `Não foi possível consultar a conta: ${error.message}`;
    buttons.forEach((button) => { button.disabled = true; });
  }
}

async function handlePlanAction(planId) {
  if (planId === "free") {
    window.location.assign("/");
    return;
  }
  showFeedback("Assinaturas pagas estão indisponíveis no momento. Nenhuma cobrança será iniciada.");
}

buttons.forEach((button) => {
  button.addEventListener("click", () => handlePlanAction(button.dataset.planAction));
});

loadAccount();
