"""Deterministic review helpers adapted from AgentPlayGround's tested skill.

This module intentionally contains no model calls.  It preserves the CSV policy
rules used by ``phenotype_review_csv_to_concept_sets.py`` and normalizes Atlas
*concept-set* exports only; full cohort-definition JSON is rejected elsewhere.
"""
from __future__ import annotations

import csv, hashlib, io, json
from collections import defaultdict
from typing import Any

POLICY_COLUMNS = ("review_include_concept", "review_include_descendants", "review_include_mapped", "review_exclude_concepts", "review_exclude_descendants", "review_exclude_mapped")

def _mark(row: dict[str, str], key: str, number: int) -> bool:
    value = (row.get(key) or "").strip()
    if not value: return False
    if value.casefold() == "x": return True
    raise ValueError(f"row {number}: {key} must be blank or x")

def parse_review_csv(data: bytes, manifest: dict[str, Any], fallback_name: str = "") -> dict[str, Any]:
    if manifest.get("schema_version") != 1: raise ValueError("unsupported_review_manifest_schema")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    fields = set(rows[0]) if rows else set(POLICY_COLUMNS)
    missing = [key for key in POLICY_COLUMNS if key not in fields]
    if missing: raise ValueError("review_csv_missing_columns:" + ",".join(missing))
    allowed = set(manifest.get("eligible_candidate_keys") or [])
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    preview, unassessed = [], []
    for number, row in enumerate(rows, 2):
        inc, inc_d, inc_m = (_mark(row, k, number) for k in POLICY_COLUMNS[:3])
        exc, exc_d, exc_m = (_mark(row, k, number) for k in POLICY_COLUMNS[3:])
        if inc and exc: raise ValueError(f"row {number}: include_and_exclude_are_mutually_exclusive")
        if (inc_d or inc_m) and not inc: raise ValueError(f"row {number}: include_descendants_or_mapped_requires_include_concept")
        if (exc_d or exc_m) and not exc: raise ValueError(f"row {number}: exclude_descendants_or_mapped_requires_exclude_concepts")
        if not (inc or exc): continue
        try: concept_id = int((row.get("concept_id") or "").strip())
        except ValueError as e: raise ValueError(f"row {number}: invalid_concept_id") from e
        name, domain = (row.get("concept_set_name") or fallback_name).strip(), (row.get("domain") or "").strip()
        if concept_id <= 0 or not name or not domain: raise ValueError(f"row {number}: concept_set_name, domain, and positive concept_id are required")
        if allowed and f"{concept_id}|{domain}" not in allowed: raise ValueError(f"row {number}: candidate is not eligible for this frozen review")
        item = {"concept_id": concept_id, "domain": domain, "include_descendants": inc_d if inc else exc_d, "include_mapped": inc_m if inc else exc_m, "is_excluded": exc}
        groups[(name, domain)].append(item)
        policy = ("Exclude" if exc else "Include") + (" + descendants" if item["include_descendants"] else "") + (" + mapped" if item["include_mapped"] else "")
        preview.append({"concept_set_name": name, "concept_id": concept_id, "concept_name": row.get("concept_name", ""), "domain": domain, "standard_concept": row.get("standard_concept", ""), "standard_concept_status": row.get("standard_concept_status", "Unknown"), "policy": policy, "assessment_status": row.get("assessment_status", ""), "precision_eligible": row.get("precision_eligible", ""), "relationship_evidence": row.get("relationship_evidence", "")})
        if inc and row.get("assessment_status", "").strip() == "not_assessed_retrieval_context": unassessed.append(preview[-1])
    if not groups: raise ValueError("no_concepts_selected")
    concept_sets = [{"name": n, "domain": d, "items": items} for (n, d), items in groups.items()]
    encoded = (json.dumps({"concept_sets": concept_sets}, sort_keys=True, indent=2) + "\n").encode()
    return {"concept_sets": concept_sets, "approval_preview": preview, "review_summary": {"selected_item_count": len(preview), "selected_concept_set_count": len(concept_sets), "unassessed_manually_included": unassessed}, "policy_sha256": hashlib.sha256(encoded).hexdigest(), "approval_json": encoded}

def normalize_atlas_concept_set(payload: dict[str, Any], name: str = "", domain: str = "") -> dict[str, Any]:
    expression = payload.get("expression") or payload.get("Expression") or payload
    if not isinstance(expression, dict) or not isinstance(expression.get("items"), list) or not expression["items"]: raise ValueError("atlas_json_missing_expression_items")
    set_name = (name or payload.get("name") or payload.get("Name") or "").strip()
    if not set_name: raise ValueError("concept_set_name_required")
    items=[]
    for raw in expression["items"]:
        concept = raw.get("concept") if isinstance(raw, dict) else None
        if not isinstance(concept, dict): raise ValueError("atlas_item_missing_concept")
        item_domain = str(concept.get("DOMAIN_ID") or concept.get("domainId") or domain or "").strip()
        if not item_domain or (domain and item_domain != domain): raise ValueError("atlas_item_domain_mismatch")
        try: cid=int(concept.get("CONCEPT_ID", concept.get("conceptId")))
        except (TypeError, ValueError) as e: raise ValueError("invalid_concept_id") from e
        if cid <= 0: raise ValueError("invalid_concept_id")
        items.append({"concept_id":cid,"domain":item_domain,"include_descendants":bool(raw.get("includeDescendants",False)),"include_mapped":bool(raw.get("includeMapped",False)),"is_excluded":bool(raw.get("isExcluded",False))})
    if len({x["domain"] for x in items}) != 1: raise ValueError("atlas_json_mixed_domains_require_separate_sets")
    return {"concept_sets":[{"name":set_name,"domain":items[0]["domain"],"items":items}],"source":"atlas_json"}
