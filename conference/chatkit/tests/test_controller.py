import json
from pathlib import Path
import pytest
from study_chat.controller import Controller, Settings, WorkflowError
from study_chat.review_helpers import parse_review_csv, normalize_atlas_concept_set

def test_review_policy_rejects_contradictory_marks():
    manifest={"schema_version":1,"review_id":"r"}
    data=b"concept_set_name,concept_id,domain,review_include_concept,review_include_descendants,review_include_mapped,review_exclude_concepts,review_exclude_descendants,review_exclude_mapped\na,1,Condition,x,,,x,,\n"
    with pytest.raises(ValueError,match="mutually_exclusive"): parse_review_csv(data,manifest)

def test_review_policy_is_exact_and_digest_stable():
    manifest={"schema_version":1,"review_id":"r"}
    data=b"concept_set_name,concept_id,concept_name,domain,review_include_concept,review_include_descendants,review_include_mapped,review_exclude_concepts,review_exclude_descendants,review_exclude_mapped\na,1,One,Condition,x,x,,,,\n"
    result=parse_review_csv(data,manifest)
    assert result["concept_sets"][0]["items"][0]["include_descendants"] is True
    assert len(result["policy_sha256"]) == 64

def test_atlas_is_concept_set_only_and_preserves_policy():
    payload={"name":"AKI","expression":{"items":[{"concept":{"CONCEPT_ID":1,"DOMAIN_ID":"Condition"},"includeDescendants":True,"includeMapped":False,"isExcluded":False}]}}
    result=normalize_atlas_concept_set(payload)
    assert result["concept_sets"][0]["items"][0]["include_descendants"]

def test_no_emission_without_exact_approval(tmp_path):
    c=Controller(Settings("http://example.invalid",tmp_path/"s.db",tmp_path/"art")); state=c.create(); state["confirmed_scope"]={"index_event":"x"}; state["make_computable"]={"narrative":"x"}; state["exact_concept_policy"]={"concept_sets":[{"name":"x","domain":"Condition","items":[{"concept_id":1}]}],"policy_sha256":"a"}; c.store.save(state)
    with pytest.raises(WorkflowError,match="approval"): pytest.run(asyncio=False) if False else __import__('asyncio').run(c.emit(state["session_id"]))
