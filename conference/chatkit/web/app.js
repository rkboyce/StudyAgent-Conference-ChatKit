const base = document.querySelector("meta[name=study-chat-base]")?.content || "/study-chat";
const api = `${base.replace(/\/$/, "")}/api`;
let sid = sessionStorage.getItem("studyChatSession") || "";
let activeTab = sessionStorage.getItem("studyChatTab") || "recommend";
let pendingRequests = 0;
let guidanceReturnTarget = null;

function setBusy(active) {
  pendingRequests = Math.max(0, pendingRequests + (active ? 1 : -1));
  const activity = document.querySelector("#activity");
  const busy = pendingRequests > 0;
  if (activity) activity.hidden = !busy;
  document.querySelector("#processing-overlay")?.toggleAttribute("hidden", !busy);
}
function setActiveTab(tab) {
  activeTab = tab;
  sessionStorage.setItem("studyChatTab", tab);
  for (const button of document.querySelectorAll("[data-workflow-tab]")) {
    const selected = button.dataset.workflowTab === tab;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  document.querySelector("#existing-pane")?.toggleAttribute("hidden", tab !== "recommend");
  document.querySelector("#computable-pane")?.toggleAttribute("hidden", tab !== "computable");
}
function highlight(element) {
  if (!element) return;
  element.classList.remove("focus-highlight");
  void element.offsetWidth;
  element.classList.add("focus-highlight");
  setTimeout(() => element.classList.remove("focus-highlight"), 1500);
}
function openGuidance(returnTarget = document.activeElement) {
  const panel = document.querySelector("#chatkit-guidance");
  guidanceReturnTarget = returnTarget;
  const goBack = document.querySelector("#guidance-return");
  if (goBack) goBack.hidden = !guidanceReturnTarget;
  document.dispatchEvent(new CustomEvent("study-chat-open-guidance"));
  document.dispatchEvent(new CustomEvent("study-chat-expand-guidance"));
  highlight(panel);
  panel?.scrollIntoView({ behavior: "smooth", block: "start" });
}
const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children) node.append(child);
  return node;
};
const clear = node => node.replaceChildren();
const json = value => JSON.stringify(value ?? {}, null, 2);
const recommendationRows = result => result?.phenotype_recommendations || result?.recommendations?.phenotype_recommendations || result?.result?.phenotype_recommendations || [];
function ohdsiHelpButton() {
  const button = el("button", { className: "secondary ohdsi-help", textContent: "/o", title: "/ohdsi: ask a workflow question" });
  button.onclick = () => openGuidance(button);
  return button;
}
async function request(path, body, options = {}) {
  setBusy(true);
  try {
    const response = await fetch(`${api}${path}`, {
      method: options.method || "POST",
      headers: body instanceof FormData ? {} : { "content-type": "application/json" },
      body: body === undefined ? undefined : (body instanceof FormData ? body : JSON.stringify(body)),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "The request could not be completed.");
    return payload;
  } finally { setBusy(false); }
}
function notice(message, error = false) {
  const box = document.querySelector("#notice");
  box.textContent = message;
  box.className = error ? "notice error" : "notice";
  const activity = document.querySelector("#activity span:last-child");
  if (activity && pendingRequests) activity.textContent = message;
}
async function ensureSession() {
  if (!sid) await newSession();
}
function roleFields(proposal) {
  const split = proposal?.intent_split || proposal || {};
  const values = { target: split.target_statement || "", comparator: split.comparator_statement || "", outcome: split.outcome_statement || "" };
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
  const revise = el("button", { className: "secondary", textContent: "Revise study question" });
  revise.onclick = () => { const intent = document.querySelector("#intent"); intent.focus(); highlight(intent); intent.scrollIntoView({ behavior: "smooth", block: "center" }); notice("Edit the study question, then find existing phenotypes again. This will replace the proposed statements."); };
  const ask = el("button", { className: "secondary", textContent: "Ask about these suggestions" });
  ask.onclick = event => openGuidance(event.currentTarget);
  section.append(button, revise, ask); return section;
}
async function beginRoleMakeComputable(state, role) {
  try {
    await ensureSession();
    setActiveTab("computable");
    const narrative = state.confirmed_role_statements[role];
    notice(`Starting the clarification checklist for a new ${role} phenotype…`);
    render(await request(`/sessions/${sid}/make-computable`, { narrative, role }));
    notice(`ACP returned the clarification checklist for the new ${role} phenotype.`);
  } catch (error) { notice(error.message, true); }
}
function recommendationDetails(candidate) {
  const details = el("details");
  details.append(el("summary", { textContent: "View recommendation details" }));
  if (candidate.justification || candidate.description) details.append(el("p", { textContent: candidate.justification || candidate.description }));
  for (const [label, value] of [
    ["Recommendation summary", candidate.recommendation_summary || candidate.short_description],
    ["Methodology", candidate.methodology_summary],
    ["Source", candidate.source_dataset],
    ["Source status", candidate.source_status],
    ["Computability", candidate.computability_status],
  ]) if (value) details.append(el("p", { textContent: `${label}: ${value}` }));
  if (Array.isArray(candidate.signals) && candidate.signals.length) details.append(el("p", { textContent: `Signals: ${candidate.signals.join(", ")}` }));
  return details;
}
function recommendations(state) {
  const section = el("section", { className: "card" });
  section.append(el("h2", { textContent: "Find an existing phenotype" }), ohdsiHelpButton());
  for (const role of Object.keys(state.confirmed_role_statements || {})) {
    const button = el("button", { textContent: `Recommendations for ${role}` });
    button.onclick = async () => { try {
      const active = state.active_role;
      if (active && active !== role && state.recommendation_results?.[active] && !window.confirm(`Show ${role} recommendations? This replaces the currently displayed ${active} recommendations.`)) return;
      notice("Requesting recommendations for " + role + "…"); const next = await request("/sessions/" + sid + "/recommendations/" + encodeURIComponent(role)); const found = recommendationRows(next.recommendation_results?.[role]); render(next); notice(found.length ? "Retrieved " + found.length + " recommendation(s) for " + role + "." : `ACP found no direct ${role} match. You can create a new ${role} phenotype instead.`); } catch (error) { notice(error.message, true); } };
    section.append(button);
  }
  const active = state.active_role;
  const result = state.recommendation_results?.[active];
  const rows = recommendationRows(result).slice(0, active === "target" ? 3 : undefined);
  if (rows.length) {
    section.append(el("h3", { textContent: `Recommendations for ${active}` }));
    const noRelevant = el("button", { className: "secondary", textContent: "No relevant options" });
    noRelevant.onclick = () => beginRoleMakeComputable(state, active);
    section.append(el("p", { className: "hint", textContent: "Click this if no relevant results are returned." }), noRelevant);
    for (const candidate of rows) {
      const candidateBox = el("article", { className: "candidate" });
      candidateBox.append(el("strong", { textContent: candidate.phenotype_name || candidate.name || candidate.phenotype_id || "Phenotype" }));
      candidateBox.append(el("code", { textContent: candidate.phenotype_id || candidate.source_id || "Source identifier unavailable" }));
      const select = el("button", { textContent: "Select this phenotype" });
      select.onclick = async () => { try { render(await request(`/sessions/${sid}/select`, { role: active, phenotype: candidate })); notice("Phenotype selected; its source identity was preserved."); } catch (error) { notice(error.message, true); } };
      candidateBox.append(select, ohdsiHelpButton(), recommendationDetails(candidate)); section.append(candidateBox);
    }
  } else if (result && active) {
    const create = el("button", { textContent: `Create a new ${active} phenotype` });
    create.onclick = () => beginRoleMakeComputable(state, active);
    section.append(el("p", { textContent: `No direct existing ${active} phenotype was returned for this statement.` }), create, ohdsiHelpButton());
  }
  return section;
}
function selectedPhenotypes(state) {
  const entries = Object.entries(state.selected_phenotypes || {});
  if (!entries.length) return null;
  const section = el("section", { className: "card" });
  section.append(el("h2", { textContent: "Selected phenotypes" }));
  for (const [role, phenotype] of entries) {
    const box = el("article", { className: "candidate" });
    box.append(el("strong", { textContent: `${role[0].toUpperCase() + role.slice(1)}: ${phenotype.phenotype_name || phenotype.name || phenotype.source_id}` }));
    box.append(el("code", { textContent: phenotype.source_id }));
    box.append(el("p", { textContent: `Computability: ${phenotype.computability_status || "not yet checked"}. Selection is recorded for this demonstration; it is not clinical approval.` }));
    const result = phenotype.definition_result;
    if (!result) {
      const inspect = el("button", { textContent: "Check available Capr/Circe artifacts" });
      inspect.onclick = async () => { try { notice(`Checking available artifacts for the selected ${role} phenotype…`); render(await request(`/sessions/${sid}/selected-definition/${encodeURIComponent(role)}`)); notice("Artifact availability was checked without creating or converting a phenotype."); } catch (error) { notice(error.message, true); } };
      box.append(inspect);
    } else if (result.status === "ok" && result.circe_json !== undefined) {
      const circe = state.generated_artifact_references?.[`selected_circe_${role}`];
      const capr = state.generated_artifact_references?.[`selected_capr_${role}`];
      box.append(el("p", { textContent: "ACP returned an existing executable definition. Review it before any use." }));
      if (circe) box.append(el("a", { href: `${api}/sessions/${sid}/artifacts/${circe}?inline=true`, target: "_blank", textContent: "View Circe JSON" }), el("a", { href: `${api}/sessions/${sid}/artifacts/${circe}`, textContent: "Download Circe JSON" }));
      if (capr) box.append(el("a", { href: `${api}/sessions/${sid}/artifacts/${capr}?inline=true`, target: "_blank", textContent: "View Capr R" }), el("a", { href: `${api}/sessions/${sid}/artifacts/${capr}`, textContent: "Download Capr R" }));
    } else {
      box.append(el("p", { textContent: "ACP did not return an existing executable artifact for this selection. You may use the create-new-phenotype path; no conversion was started automatically." }));
      box.append(el("details", {}, [el("summary", { textContent: "Artifact-check details" }), el("pre", { textContent: json(result) })]));
    }
    section.append(box);
  }
  return section;
}
function optionSelect(id, label, choices, value = "") {
  const input = el("select", { id });
  input.append(el("option", { value: "", textContent: `Choose ${label.toLowerCase()}…` }));
  for (const [choiceValue, choiceLabel] of choices) input.append(el("option", { value: choiceValue, textContent: choiceLabel }));
  input.value = value || "";
  return { input, label: el("label", { textContent: label }, [input]) };
}
function scopeDraftControls(draftResponse, narrative) {
  const draft = draftResponse?.scope_draft || {};
  const wrap = el("div", { className: "scope" });
  const errorBox = el("p", { id: "scope-form-error", className: "form-error", role: "alert" });
  const hasSuggestedValues = Boolean(draft.index_event || draft.domain || draft.entry_limit || draft.exit_strategy);
  wrap.append(el("h3", { textContent: "Review and confirm the phenotype scope" }), ohdsiHelpButton());
  wrap.append(el("p", { textContent: hasSuggestedValues ? "These are advisory starting values. Review every decision below; submitting the form is the explicit scope confirmation." : "AI needs additional clinical decisions before it can safely suggest scope values. Read the clarification below, refine the statement, or ask /ohdsi for help." }));
  if (draft.rationale) wrap.append(el("p", { textContent: draft.rationale }));
  const questions = Array.isArray(draftResponse?.questions) ? draftResponse.questions : [];
  if (questions.length) wrap.append(el("p", { textContent: questions.join(" ") }));
  const warnings = Array.isArray(draft.warnings) ? draft.warnings : [];
  if (warnings.length) wrap.append(el("ul", {}, warnings.map(warning => el("li", { textContent: warning }))));
  const refreshSuggestions = el("button", { className: "secondary", textContent: "Get / refresh AI scope suggestions" });
  refreshSuggestions.onclick = async () => {
    try {
      notice("Requesting advisory AI scope suggestions…");
      const next = await request(`/sessions/${sid}/make-computable`, { narrative });
      const nextDraft = next.make_computable?.clarification?.scope_draft || {};
      render(next);
      notice(nextDraft.index_event || nextDraft.domain || nextDraft.entry_limit || nextDraft.exit_strategy
        ? "ACP returned advisory AI scope suggestions. Review each value before confirmation."
        : "ACP needs additional clinical clarification before it can safely suggest scope values. Review the explanation above or ask /ohdsi.");
    } catch (error) { notice(error.message, true); }
  };
  wrap.append(refreshSuggestions);
  const indexEvent = el("input", { id: "scope-index-event", value: draft.index_event || "", placeholder: "Plain-language index event" });
  const priorObservation = el("input", { id: "scope-prior-observation", type: "number", min: "0", value: draft.prior_observation_days ?? "", placeholder: "Days" });
  const domain = optionSelect("scope-domain", "OMOP event domain", [["Condition", "Condition"], ["Drug", "Drug"], ["Procedure", "Procedure"], ["Measurement", "Measurement"], ["Observation", "Observation"], ["Visit", "Visit"], ["Device", "Device"]], draft.domain);
  const entryLimit = optionSelect("scope-entry-limit", "Entry limit", [["First", "First qualifying event"], ["All", "All qualifying events"]], draft.entry_limit);
  const boundary = optionSelect("scope-index-boundary", "Index-day boundary", [["included", "Include the index day"], ["excluded", "Exclude the index day"]]);
  const windows = optionSelect("scope-windows", "Additional temporal windows", [["none", "No additional temporal windows"]], "none");
  const exit = optionSelect("scope-exit", "Cohort exit rule", [["observation", "End of observation"], ["end_of_observation", "End of observation"]], draft.exit_strategy);
  const visitOverlap = optionSelect("scope-visit-overlap", "Visit-overlap requirement", [["false", "No visit-overlap requirement"], ["true", "Require overlap with a visit"]]);
  const fields = [indexEvent, domain.input, entryLimit.input, priorObservation, boundary.input, windows.input, exit.input, visitOverlap.input];
  wrap.append(el("label", { textContent: "Plain-language index event" }, [indexEvent]), domain.label, entryLimit.label, el("label", { textContent: "Required prior observation (days)" }, [priorObservation]), boundary.label, windows.label, exit.label, visitOverlap.label, errorBox);
  const confirm = el("button", { textContent: "Confirm this exact scope and retrieve candidates" });
  confirm.onclick = async () => {
    const missing = fields.filter(input => input.value === "" || input.value === null);
    for (const input of fields) { input.classList.remove("invalid"); input.removeAttribute("aria-invalid"); }
    if (missing.length) {
      for (const input of missing) { input.classList.add("invalid"); input.setAttribute("aria-invalid", "true"); }
      errorBox.textContent = "Complete the highlighted scope decisions before confirming.";
      missing[0].focus(); missing[0].scrollIntoView({ behavior: "smooth", block: "center" });
      notice("Complete every scope decision before confirming it.", true); return;
    }
    errorBox.textContent = "";
    const event = indexEvent.value.trim();
    const scope = { index_event: event, criterion_domains: { [event]: domain.input.value }, entry_limit: entryLimit.input.value, prior_observation: Number(priorObservation.value), index_day_boundary: boundary.input.value, windows: windows.input.value, exit_strategy: exit.input.value, era_days: 0, visit_overlap: visitOverlap.input.value === "true" };
    if (draft.vocabulary_restriction) scope.criterion_vocabularies = { [event]: [draft.vocabulary_restriction] };
    try { render(await request(`/sessions/${sid}/scope`, { scope, candidate_limit: 20 })); notice("Scope submitted as an explicit confirmation."); }
    catch (error) { notice(`Scope was not submitted: ${error.message}`, true); }
  };
  wrap.append(confirm);
  wrap.append(el("details", {}, [el("summary", { textContent: "View raw ACP advisory response" }), el("pre", { textContent: json(draftResponse) })]));
  return wrap;
}
function computableWorkflow(state) {
  const section = el("section", { className: "card" });
  section.append(el("h2", { textContent: "Create a new computable phenotype" }), ohdsiHelpButton());
  const draft = state.make_computable?.clarification;
  const scopeStarted = Boolean(draft && state.current_stage === "scope");
  if (scopeStarted) {
    section.append(el("p", { textContent: "A scope statement has been started for this new phenotype. Complete and confirm every decision below before concept candidates can be retrieved." }));
    section.append(el("p", { className: "hint", textContent: `Statement: ${state.make_computable?.narrative || "Not available"}` }));
    section.append(scopeDraftControls(draft, state.make_computable?.narrative || ""));
  } else {
    section.append(el("p", { textContent: "Use this when no existing phenotype is suitable. It begins with a reviewable scope checklist; it does not create artifacts until later concept-policy approval." }));
    const narrative = el("textarea", { id: "narrative", value: state.make_computable?.narrative || "", placeholder: "Describe the phenotype in plain clinical language." });
    const clarify = el("button", { textContent: "Start scope checklist" });
    clarify.onclick = async () => { try { await ensureSession(); setActiveTab("computable"); notice("Requesting the scope checklist…"); render(await request(`/sessions/${sid}/make-computable`, { narrative: narrative.value.trim() })); notice("ACP returned an advisory scope checklist. Review and explicitly confirm each decision."); } catch (error) { notice(error.message, true); } };
    section.append(narrative, clarify);
  }
  if (["review", "approved"].includes(state.current_stage)) section.append(reviewControls(state));
  return section;
}
function reviewControls(state) {
  const wrap = el("div", { className: "review" });
  const review = state.make_computable?.review || {};
  wrap.append(el("h3", { textContent: "Review and approve concept policies" }), ohdsiHelpButton());
  if (state.concept_review_id && review.review_urls?.candidates_csv && review.review_urls?.manifest) {
    wrap.append(el("p", { textContent: "Scope is confirmed. ACP returned a frozen concept-candidate review session. Next, download the package, review concept inclusion and descendant/mapped/exclusion policies, then upload the marked CSV for exact-policy approval." }));
  } else {
    wrap.append(el("p", { textContent: "Scope is confirmed, but ACP did not provide a review package. Do not approve or emit artifacts. Request a new review session or inspect Session details." }));
  }
  const freeze = el("button", { textContent: "Freeze review CSV and manifest" });
  freeze.onclick = async () => { try { render(await request(`/sessions/${sid}/review/freeze`, {})); notice("Frozen review package saved in this private session."); } catch (error) { notice(error.message, true); } };
  wrap.append(freeze);
  const refs = state.review_artifact_references || {};
  if (refs.csv && refs.manifest) {
    wrap.append(el("p", { textContent: "Frozen package ready:" }), el("a", { href: `${api}/sessions/${sid}/artifacts/${refs.csv}`, textContent: "Download frozen review CSV" }), el("a", { href: `${api}/sessions/${sid}/artifacts/${refs.manifest}`, textContent: "Download review manifest" }));
    const upload = el("input", { type: "file", accept: ".csv" });
    upload.onchange = async () => {
      if (!upload.files?.[0]) return;
      const data = new FormData(); data.append("file", upload.files[0]);
      try { render(await request(`/sessions/${sid}/review/upload`, data)); notice("CSV policy validated. Review the exact digest below."); } catch (error) { notice(error.message, true); }
    };
    wrap.append(el("label", { textContent: "Upload reviewed CSV" }, [upload]));
  }
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
  const section = el("section", { className: "card" }); section.append(el("h2", { textContent: "Artifacts" }), ohdsiHelpButton());
  const refs = state.generated_artifact_references;
  for (const name of ["capr.R", "circe.json"]) {
    if ((name === "capr.R" && refs.capr) || (name === "circe.json" && (refs.circe || refs.selected_circe))) section.append(el("a", { href: `${api}/sessions/${sid}/artifacts/${name}`, textContent: `Download ${name}` }));
  }
  section.append(el("p", { textContent: "These are technically validated artifacts only. Clinical validity and database-level diagnostics still require appropriate review." }));
  return section;
}
function render(state) {
  const existing = document.querySelector("#existing-pane");
  const computable = document.querySelector("#computable-pane");
  clear(existing); clear(computable);
  const stage = document.querySelector("#stage");
  if (stage) stage.textContent = state.current_stage || "Start with a research question";
  document.querySelector("#getting-started").hidden = Boolean(state.study_intent);
  if (state.intent_proposal && !Object.keys(state.confirmed_role_statements || {}).length) existing.append(roleFields(state.intent_proposal));
  if (Object.keys(state.confirmed_role_statements || {}).length) existing.append(recommendations(state));
  const selected = selectedPhenotypes(state); if (selected) existing.append(selected);
  computable.append(computableWorkflow(state));
  const output = artifacts(state); if (output) computable.append(output);
  computable.append(el("details", {}, [el("summary", { textContent: "Session details" }), el("pre", { textContent: json(state) })]));
  setActiveTab(activeTab);
}
async function newSession() {
  const state = await request("/sessions", {});
  sid = state.session_id; sessionStorage.setItem("studyChatSession", sid);
  document.querySelector("#new").hidden = false;
  document.dispatchEvent(new CustomEvent("study-chat-session", { detail: { sessionId: sid } }));
  render(state); notice("New private session created.");
}
document.querySelector("#guidance-shortcut").onclick = event => openGuidance(event.currentTarget);
document.querySelector("#guidance-return").onclick = () => {
  const target = guidanceReturnTarget;
  guidanceReturnTarget = null;
  document.querySelector("#guidance-return").hidden = true;
  if (target?.isConnected) {
    target.focus(); highlight(target);
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }
};
document.querySelector("#new").onclick = () => newSession().catch(error => notice(error.message, true));
document.querySelector("#start").onclick = async () => {
  try { if (!sid) await newSession(); setActiveTab("recommend"); render(await request(`/sessions/${sid}/intent`, { study_intent: document.querySelector("#intent").value.trim(), workflow_type: "comparative" })); }
  catch (error) { notice(error.message, true); }
};
for (const button of document.querySelectorAll("[data-workflow-tab]")) {
  button.onclick = async () => { try { if (button.dataset.workflowTab === "computable") await ensureSession(); setActiveTab(button.dataset.workflowTab); } catch (error) { notice(error.message, true); } };
}
if (sid) { document.querySelector("#new").hidden = false; fetch(`${api}/sessions/${sid}`).then(r => r.ok ? r.json() : newSession()).then(render).catch(() => newSession()); }
else render({ current_stage: "intent", generated_artifact_references: {} });
