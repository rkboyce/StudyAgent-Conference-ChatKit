const base = document.querySelector("meta[name=study-chat-base]")?.content || "/study-chat";
const api = `${base.replace(/\/$/, "")}/api`;
let sid = sessionStorage.getItem("studyChatSession") || "";

const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children) node.append(child);
  return node;
};
const clear = node => node.replaceChildren();
const json = value => JSON.stringify(value ?? {}, null, 2);
const recommendationRows = result => result?.phenotype_recommendations || result?.recommendations?.phenotype_recommendations || result?.result?.phenotype_recommendations || [];
async function request(path, body, options = {}) {
  const response = await fetch(`${api}${path}`, {
    method: options.method || "POST",
    headers: body instanceof FormData ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : (body instanceof FormData ? body : JSON.stringify(body)),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "The request could not be completed.");
  return payload;
}
function notice(message, error = false) {
  const box = document.querySelector("#notice");
  box.textContent = message;
  box.className = error ? "notice error" : "notice";
}
function roleFields(proposal) {
  const split = proposal?.intent_split || proposal || {};
  const values = {
    target: split.target_statement || "",
    comparator: split.comparator_statement || "",
    outcome: split.outcome_statement || "",
  };
  const section = el("section", { className: "card" });
  section.append(el("h2", { textContent: "Confirm cohort statements" }));
  section.append(el("p", { textContent: "These are suggestions only. Edit every statement that needs revision, then explicitly confirm them." }));
  for (const role of ["target", "comparator", "outcome"]) {
    const input = el("textarea", { id: `role-${role}`, value: values[role], placeholder: `${role} cohort statement` });
    section.append(el("label", { textContent: role[0].toUpperCase() + role.slice(1) }, [input]));
  }
  const button = el("button", { textContent: "Confirm statements" });
  button.onclick = async () => {
    const statements = Object.fromEntries(["target", "comparator", "outcome"].map(role => [role, document.querySelector(`#role-${role}`).value.trim()]).filter(([, value]) => value));
    try { render(await request(`/sessions/${sid}/roles`, { statements })); notice("Cohort statements confirmed."); }
    catch (error) { notice(error.message, true); }
  };
  section.append(button); return section;
}
function recommendations(state) {
  const section = el("section", { className: "card" });
  section.append(el("h2", { textContent: "Find an existing phenotype" }));
  for (const role of Object.keys(state.confirmed_role_statements || {})) {
    const button = el("button", { textContent: `Recommendations for ${role}` });
    button.onclick = async () => { try { notice("Requesting recommendations for " + role + "…"); const next = await request("/sessions/" + sid + "/recommendations/" + encodeURIComponent(role)); const found = recommendationRows(next.recommendation_results?.[role]); render(next); notice(found.length ? "Retrieved " + found.length + " recommendation(s) for " + role + "." : "ACP returned no displayable recommendations for " + role + "; expand Session details for the raw response."); } catch (error) { notice(error.message, true); } };
    const create = el("button", { textContent: "No — create a new " + role + " phenotype" });
    create.onclick = async () => {
      try {
        const narrative = state.confirmed_role_statements[role];
        notice("Starting the clarification checklist for a new " + role + " phenotype…");
        render(await request(`/sessions/${sid}/make-computable`, { narrative, role }));
        notice("ACP returned the clarification checklist for the new " + role + " phenotype.");
      } catch (error) { notice(error.message, true); }
    };
    section.append(button, create);
  }
  const active = state.active_role;
  const result = state.recommendation_results?.[active];
  const rows = recommendationRows(result);
  if (rows.length) {
    section.append(el("h3", { textContent: `Recommendations for ${active}` }));
    for (const candidate of rows) {
      const candidateBox = el("article", { className: "candidate" });
      candidateBox.append(el("strong", { textContent: candidate.phenotype_name || candidate.name || candidate.phenotype_id || "Phenotype" }));
      candidateBox.append(el("p", { textContent: candidate.justification || candidate.description || "No ACP description supplied." }));
      candidateBox.append(el("code", { textContent: candidate.phenotype_id || candidate.source_id || "Source identifier unavailable" }));
      const select = el("button", { textContent: "Select this phenotype" });
      select.onclick = async () => { try { render(await request(`/sessions/${sid}/select`, { role: active, phenotype: candidate })); notice("Phenotype selected; its source identity was preserved."); } catch (error) { notice(error.message, true); } };
      candidateBox.append(select); section.append(candidateBox);
    }
  }
  return section;
}
function computableWorkflow(state) {
  const section = el("section", { className: "card" });
  section.append(el("h2", { textContent: "Make a phenotype computable" }));
  const narrative = el("textarea", { id: "narrative", value: state.make_computable?.narrative || "", placeholder: "Describe the phenotype in plain clinical language." });
  const clarify = el("button", { textContent: "Request clarification checklist" });
  clarify.onclick = async () => { try { render(await request(`/sessions/${sid}/make-computable`, { narrative: narrative.value.trim() })); } catch (error) { notice(error.message, true); } };
  section.append(narrative, clarify);
  const draft = state.make_computable?.clarification;
  if (draft) {
    section.append(el("h3", { textContent: "ACP clarification / advisory scope draft" }));
    section.append(el("pre", { textContent: json(draft) }));
    const scope = el("textarea", { id: "scope-json", value: json(state.confirmed_scope || draft.scope_draft || {}), placeholder: "Paste the complete, reviewed scope JSON." });
    const confirm = el("button", { textContent: "Confirm exact scope and retrieve candidates" });
    confirm.onclick = async () => {
      try { render(await request(`/sessions/${sid}/scope`, { scope: JSON.parse(scope.value), candidate_limit: 20 })); notice("Scope submitted as an explicit confirmation."); }
      catch (error) { notice(`Scope was not submitted: ${error.message}`, true); }
    };
    section.append(el("p", { textContent: "Review the draft first. It is not confirmed until you use the confirmation action below." }), scope, confirm);
  }
  if (["review", "approved"].includes(state.current_stage)) section.append(reviewControls(state));
  return section;
}
function reviewControls(state) {
  const wrap = el("div", { className: "review" });
  wrap.append(el("h3", { textContent: "Concept-policy review" }));
  wrap.append(el("p", { textContent: "Download the frozen review package, mark only review_* CSV columns, then upload it. Approval is required before artifact generation." }));
  const freeze = el("button", { textContent: "Freeze review CSV and manifest" });
  freeze.onclick = async () => { try { render(await request(`/sessions/${sid}/review/freeze`, {})); notice("Frozen review package saved in this private session."); } catch (error) { notice(error.message, true); } };
  const upload = el("input", { type: "file", accept: ".csv" });
  upload.onchange = async () => {
    if (!upload.files?.[0]) return;
    const data = new FormData(); data.append("file", upload.files[0]);
    try { render(await request(`/sessions/${sid}/review/upload`, data)); notice("CSV policy validated. Review the exact digest below."); } catch (error) { notice(error.message, true); }
  };
  wrap.append(freeze, upload);
  const policy = state.exact_concept_policy;
  if (policy?.policy_sha256) {
    wrap.append(el("pre", { textContent: json(policy) }));
    const approve = el("button", { textContent: "Approve this exact policy digest" });
    approve.onclick = async () => { try { render(await request(`/sessions/${sid}/approve`, { digest: policy.policy_sha256 })); } catch (error) { notice(error.message, true); } };
    wrap.append(approve);
  }
  if (state.current_stage === "approved") {
    const emit = el("button", { textContent: "Generate validated artifacts" });
    emit.onclick = async () => { try { render(await request(`/sessions/${sid}/emit`, {})); } catch (error) { notice(error.message, true); } };
    wrap.append(emit);
  }
  return wrap;
}
function artifacts(state) {
  if (!state.generated_artifact_references) return null;
  const section = el("section", { className: "card" }); section.append(el("h2", { textContent: "Artifacts" }));
  const refs = state.generated_artifact_references;
  for (const name of ["capr.R", "circe.json"]) {
    if ((name === "capr.R" && refs.capr) || (name === "circe.json" && (refs.circe || refs.selected_circe))) section.append(el("a", { href: `${api}/sessions/${sid}/artifacts/${name}`, textContent: `Download ${name}` }));
  }
  section.append(el("p", { textContent: "These are technically validated artifacts only. Clinical validity and database-level diagnostics still require appropriate review." }));
  return section;
}
function render(state) {
  const content = document.querySelector("#workflow"); clear(content);
  document.querySelector("#stage").textContent = state.current_stage || "Start with a research question";
  if (state.intent_proposal && !Object.keys(state.confirmed_role_statements || {}).length) content.append(roleFields(state.intent_proposal));
  if (Object.keys(state.confirmed_role_statements || {}).length) content.append(recommendations(state));
  content.append(computableWorkflow(state));
  const output = artifacts(state); if (output) content.append(output);
  content.append(el("details", {}, [el("summary", { textContent: "Session details" }), el("pre", { textContent: json(state) })]));
}
async function newSession() { const state = await request("/sessions", {}); sid = state.session_id; sessionStorage.setItem("studyChatSession", sid); document.dispatchEvent(new CustomEvent("study-chat-session", { detail: { sessionId: sid } })); render(state); notice("New private session created."); }
document.querySelector("#new").onclick = () => newSession().catch(error => notice(error.message, true));
document.querySelector("#start").onclick = async () => {
  try { if (!sid) await newSession(); render(await request(`/sessions/${sid}/intent`, { study_intent: document.querySelector("#intent").value.trim(), workflow_type: "comparative" })); }
  catch (error) { notice(error.message, true); }
};
if (sid) fetch(`${api}/sessions/${sid}`).then(r => r.ok ? r.json() : newSession()).then(render).catch(() => newSession());
