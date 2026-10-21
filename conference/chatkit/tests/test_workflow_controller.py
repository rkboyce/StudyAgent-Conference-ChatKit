import asyncio
import json

import pytest

from study_chat.controller import ACP, Controller, Settings, WorkflowError


class FakeACP:
    def __init__(self):
        self.calls = []

    async def post(self, flow, payload):
        self.calls.append((flow, payload))
        if flow == "phenotype_intent_split":
            return {"status": "ok", "intent_split": {"target_statement": "Naproxen exposure", "outcome_statement": "Myocardial infarction"}}
        if flow == "phenotype_recommendation":
            return {"status": "ok", "phenotype_recommendations": [{"phenotype_id": "ohdsi:44", "phenotype_name": "Myocardial infarction", "circe_json": {"ConceptSets": []}}]}
        if flow == "phenotype_definition":
            return {"status": "ok", "phenotype_id": payload["phenotype_id"], "circe_json": {"ConceptSets": []}, "conversion": {"performed": False, "capr_code": None}}
        if flow == "phenotype_make_computable" and not payload["confirmed_scope"]:
            return {"status": "needs_clarification", "scope_draft": {"index_event": "Acute kidney injury"}}
        if flow == "phenotype_make_computable" and payload["concept_review_mode"] == "required":
            return {"status": "needs_concept_review", "review_id": "review-1", "review_urls": {"candidates_csv": "/flows/phenotype_make_computable/reviews/review-1/candidates.csv", "manifest": "/flows/phenotype_make_computable/reviews/review-1/manifest"}}
        if flow == "phenotype_make_computable" and payload["concept_review_mode"] == "provided_only":
            return {"status": "ok", "capr": {"source": "cohort <- function() {}"}, "circe_json": {"ConceptSets": []}}
        raise AssertionError((flow, payload))


def controller(tmp_path):
    instance = Controller(Settings("https://acp.example.test", tmp_path / "sessions.db", tmp_path / "artifacts"))
    instance.acp = FakeACP()
    return instance


def test_question_to_role_selection_and_recommendation_is_persisted(tmp_path):
    c = controller(tmp_path)
    state = c.create()
    state = asyncio.run(c.capture_intent(state["session_id"], "Does naproxen increase MI risk?"))
    assert state["current_stage"] == "intent"
    state = c.confirm_roles(state["session_id"], {"target": "Naproxen exposure", "comparator": "Ibuprofen exposure", "outcome": "Myocardial infarction"})
    state = asyncio.run(c.recommend(state["session_id"], "outcome"))
    assert state["recommendation_results"]["outcome"]["status"] == "ok"
    recommendation_call = c.acp.calls[-1][1]
    assert recommendation_call["workflow_type"] == "cohort_methods"
    assert recommendation_call["candidate_limit"] == 5
    assert recommendation_call["max_results"] == 5
    state = asyncio.run(c.recommend(state["session_id"], "target"))
    assert c.acp.calls[-1][1]["max_results"] == 3
    state = c.select_phenotype(state["session_id"], "outcome", state["recommendation_results"]["outcome"]["phenotype_recommendations"][0])
    assert state["selected_phenotypes"]["outcome"]["source_id"] == "ohdsi:44"
    assert (tmp_path / "artifacts" / state["session_id"] / "circe.json").exists()
    state = asyncio.run(c.retrieve_selected_definition(state["session_id"], "outcome"))
    assert state["generated_artifact_references"]["selected_circe_outcome"] == "selected_outcome_circe.json"
    assert (tmp_path / "artifacts" / state["session_id"] / "selected_outcome_circe.json").exists()


def test_revising_role_statements_invalidates_downstream_state(tmp_path):
    c = controller(tmp_path)
    state = c.create()
    sid = state["session_id"]
    state = c.confirm_roles(sid, {"target": "Naproxen exposure", "outcome": "Myocardial infarction"})
    state = c.select_phenotype(sid, "target", {"phenotype_id": "ohdsi:44", "circe_json": {"ConceptSets": []}})
    state.update(confirmed_scope={"index_event": "Naproxen"}, concept_review_id="review-old", exact_concept_policy={"policy_sha256": "old"}, approval_record={"policy_sha256": "old"})
    c.store.save(state)
    revised = c.confirm_roles(sid, {"target": "Ibuprofen exposure", "outcome": "Myocardial infarction"})
    assert revised["selected_phenotypes"] == {}
    assert revised["confirmed_scope"] is None
    assert revised["concept_review_id"] is None
    assert revised["approval_record"] is None


def test_make_computable_requires_exact_policy_approval_before_emission(tmp_path):
    c = controller(tmp_path)
    state = c.create()
    sid = state["session_id"]
    c.confirm_roles(sid, {"target": "Naproxen exposure", "outcome": "Acute kidney injury"})
    state = asyncio.run(c.request_scope(sid, "Acute kidney injury", "outcome"))
    assert state["active_role"] == "outcome"
    assert state["confirmed_scope"] is None
    scope = {"index_event": "Acute kidney injury", "criterion_domains": {"Acute kidney injury": "Condition"}, "entry_limit": "First", "prior_observation": 365, "index_day_boundary": "included", "windows": "none", "exit_strategy": "observation"}
    state = asyncio.run(c.confirm_scope(sid, scope))
    assert state["current_stage"] == "review"
    state["exact_concept_policy"] = {"concept_sets": [{"name": "AKI", "domain": "Condition", "items": [{"concept_id": 1, "domain": "Condition", "include_descendants": False, "include_mapped": False, "is_excluded": False}]}], "policy_sha256": "policy-a"}
    c.store.save(state)
    with pytest.raises(WorkflowError, match="approval"):
        asyncio.run(c.emit(sid))
    c.approve_policy(sid, "policy-a")
    emitted = asyncio.run(c.emit(sid))
    assert emitted["current_stage"] == "complete"
    assert (tmp_path / "artifacts" / sid / "capr.R").exists()
    assert not any(flow == "phenotype_make_computable" and call["concept_review_mode"] == "provided_only" for flow, call in c.acp.calls[:-1])


def test_review_upload_rejects_manifest_for_another_review(tmp_path):
    c = controller(tmp_path)
    state = c.create()
    sid = state["session_id"]
    state["concept_review_id"] = "review-current"
    c.store.save(state)
    root = tmp_path / "artifacts" / sid
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"schema_version": 1, "review_id": "review-old"}))
    with pytest.raises(WorkflowError, match="review_manifest_mismatch"):
        c.ingest_review_csv(sid, b"concept_set_name,concept_id,domain,review_include_concept,review_include_descendants,review_include_mapped,review_exclude_concepts,review_exclude_descendants,review_exclude_mapped\nAKI,1,Condition,x,,,,,\n")


def test_review_urls_keep_the_configured_acp_path_prefix(tmp_path):
    acp = ACP(Settings("https://atlas.napdi.org/StudyAgent", tmp_path / "sessions.db", tmp_path / "artifacts"))
    assert str(acp.resolve_trusted_url("/flows/phenotype_make_computable/reviews/a/candidates.csv")) == "https://atlas.napdi.org/StudyAgent/flows/phenotype_make_computable/reviews/a/candidates.csv"
    with pytest.raises(WorkflowError, match="untrusted_review_url"):
        acp.resolve_trusted_url("https://example.test/flows/phenotype_make_computable/reviews/a/candidates.csv")


def test_freezing_empty_review_package_records_atlas_handoff(tmp_path):
    c = controller(tmp_path)
    state = c.create()
    sid = state["session_id"]
    state["concept_review_id"] = "review-empty"
    state["make_computable"] = {"review": {"review_urls": {"candidates_csv": "/flows/phenotype_make_computable/reviews/review-empty/candidates.csv", "manifest": "/flows/phenotype_make_computable/reviews/review-empty/manifest"}}}
    c.store.save(state)

    async def empty_package(url):
        if str(url).endswith("candidates.csv"):
            return b"concept_set_name,concept_id,domain\n"
        return json.dumps({"schema_version": 1, "review_id": "review-empty", "eligible_candidate_keys": []}).encode()

    c.acp.get_trusted = empty_package
    frozen = asyncio.run(c.freeze_review_package(sid))
    refs = frozen["review_artifact_references"]
    assert refs["candidate_count"] == 0
    assert refs["no_vocabulary_candidates"] is True
