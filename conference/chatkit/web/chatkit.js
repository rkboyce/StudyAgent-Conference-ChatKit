const base = document.querySelector("meta[name=study-chat-base]")?.content || "/study-chat";
const api = `${base.replace(/\/$/, "")}/api`;
let mountedSession = "";

function message(text) {
  const panel = document.querySelector("#chatkit-guidance");
  if (panel) panel.querySelector(".hint").textContent = text;
}
function updateGuidanceSummary() {
  const panel = document.querySelector("#chatkit-guidance");
  const summary = document.querySelector("#guidance-summary");
  if (panel && summary) summary.textContent = panel.open ? "Click to hide conversational guidance" : "Expand for conversational guidance";
}

function setChatExpanded(expanded) {
  const host = document.querySelector("#chatkit");
  const button = document.querySelector("#chatkit-expand");
  if (!host || !button) return;
  host.classList.toggle("tall", expanded);
  button.textContent = expanded ? "Use compact chat" : "Expand chat";
}

async function mountChatKit(sessionId) {
  if (!sessionId || mountedSession === sessionId) return;
  const config = await fetch(`${api}/config`).then(response => response.ok ? response.json() : {});
  if (!config.chatkit_domain_key) {
    message("Conversational guidance is not enabled for this deployment. The review-gated workflow controls below remain available.");
    return;
  }
  await Promise.race([customElements.whenDefined("openai-chatkit"), new Promise((_, reject) => setTimeout(() => reject(new Error("chatkit_load_timeout")), 5000))]);
  const host = document.querySelector("#chatkit");
  if (!host) return;
  host.replaceChildren();
  const chatkit = document.createElement("openai-chatkit");
  chatkit.setOptions({
    api: {
      url: `${base.replace(/\/$/, "")}/chatkit`,
      domainKey: config.chatkit_domain_key,
      fetch: (input, init = {}) => {
        const headers = new Headers(init.headers || {});
        headers.set("X-Study-Session", sessionId);
        return fetch(input, { ...init, headers });
      },
    },
    theme: "light",
  });
  chatkit.addEventListener("chatkit.error", event => { console.error("ChatKit error", event.detail?.error || event.detail); host.replaceChildren(); host.classList.remove("ready"); message("Conversational guidance is temporarily unavailable. See the browser console for its exact error; workflow state is unchanged."); });
  host.append(chatkit); host.classList.add("ready"); document.querySelector("#chatkit-expand").hidden = false; setChatExpanded(false);
  mountedSession = sessionId;
  message("ChatKit is ready. Ask a workflow question in the conversation panel below; approvals remain in the controls above.");
}

document.querySelector("#chatkit-guidance")?.addEventListener("toggle", updateGuidanceSummary);
document.querySelector("#chatkit-expand")?.addEventListener("click", () => setChatExpanded(!document.querySelector("#chatkit")?.classList.contains("tall")));
document.addEventListener("study-chat-open-guidance", () => { const panel = document.querySelector("#chatkit-guidance"); if (panel) { panel.open = true; updateGuidanceSummary(); } });
updateGuidanceSummary();
document.addEventListener("study-chat-session", event => mountChatKit(event.detail.sessionId).catch(() => message("Conversational guidance could not be initialized.")));
mountChatKit(sessionStorage.getItem("studyChatSession") || "").catch(() => message("Conversational guidance could not be initialized."));
