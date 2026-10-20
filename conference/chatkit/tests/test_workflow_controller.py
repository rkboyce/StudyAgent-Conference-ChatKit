import asyncio
import json

import pytest

from study_chat.controller import Controller, Settings, WorkflowError


class FakeACP:
    def __init__(self):
        self.calls = []

    async def post(self, flow, payload):
        self.calls.append((flow, payload))
        if flow == "phenotype_intent_split":
            return {"status": "ok", "intent_split": {"target_statement": "Naproxen exposure", "outcome_statement": "Myocardial infarction"}}
        if flow == "phenotype_recommendation":
            return {"status": "ok", "phenotype_recommendations": [{"phenotype_id": "ohdsi:44", "phenotype_name": "Myocardial infarction", "circe_json": {"ConceptSets": []}}]}
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
    state = c.select_phenotype(state["session_id"], "outcome", state["recommendation_results"]["outcome"]["phenotype_recommendations"][0])
    assert state["selected_phenotypes"]["outcome"]["source_id"] == "ohdsi:44"
    assert (tmp_path / "artifacts" / state["session_id"] / "circe.json").exists()


def test_make_computable_requires_exact_policy_approval_before_emission(tmp_path):
    c = controller(tmp_path)
    state = c.create()
    sid = state["session_id"]
    state = asyncio.run(c.request_scope(sid, "Acute kidney injury"))
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
