const base = document.querySelector("meta[name=study-chat-base]")?.content || "/study-chat";
const api = `${base.replace(/\/$/, "")}/api`;
let mountedSession = "";

function message(text) {
  const panel = document.querySelector("#chatkit-guidance");
  if (panel) panel.querySelector(".hint").textContent = text;
}

async function mountChatKit(sessionId) {
  if (!sessionId || mountedSession === sessionId) return;
  await Promise.race([customElements.whenDefined("openai-chatkit"), new Promise((_, reject) => setTimeout(() => reject(new Error("chatkit_load_timeout")), 5000))]);
  const host = document.querySelector("#chatkit");
  if (!host) return;
  host.replaceChildren();
  const chatkit = document.createElement("openai-chatkit");
  chatkit.setOptions({
    apiURL: `${base.replace(/\/$/, "")}/chatkit`,
    fetch: (input, init = {}) => {
      const headers = new Headers(init.headers || {});
      headers.set("X-Study-Session", sessionId);
      return fetch(input, { ...init, headers });
    },
    theme: "light",
  });
  chatkit.addEventListener("chatkit.error", () => { host.replaceChildren(); host.classList.remove("ready"); message("Conversational guidance is temporarily unavailable. Your deterministic workflow state is unchanged."); });
  host.append(chatkit); host.classList.add("ready");
  mountedSession = sessionId;
  message("Ask questions here; approvals and other workflow decisions remain in the controls below.");
}

document.addEventListener("study-chat-session", event => mountChatKit(event.detail.sessionId).catch(() => message("Conversational guidance could not be initialized.")));
mountChatKit(sessionStorage.getItem("studyChatSession") || "").catch(() => message("Conversational guidance could not be initialized."));
