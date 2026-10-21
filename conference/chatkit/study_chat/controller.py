from __future__ import annotations

import hashlib, json, secrets, shutil, sqlite3, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import httpx
from .review_helpers import parse_review_csv, normalize_atlas_concept_set

STAGES = {"intent": "Confirm cohort statements", "recommendations": "Review phenotype recommendations", "scope": "Confirm computable-phenotype scope", "review": "Review exact concept policy", "approved": "Emit validated artifacts", "complete": "Artifacts ready"}

class WorkflowError(ValueError): pass

@dataclass
class Settings:
    acp_url: str; db_path: Path; artifact_root: Path; timeout: float = 45; session_ttl: int = 86400

class Store:
    def __init__(self, path: Path, session_ttl: int):
        self._ttl = session_ttl
        path.parent.mkdir(parents=True, exist_ok=True); self.db=sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL, expires INTEGER NOT NULL)"); self.db.commit()
    def create(self) -> dict[str, Any]:
        sid=secrets.token_urlsafe(32); state={"session_id":sid,"workflow_type":"phenotype","study_intent":"","confirmed_role_statements":{},"active_role":None,"current_stage":"intent","selected_phenotypes":{},"recommendation_results":{},"confirmed_scope":None,"concept_review_id":None,"review_artifact_references":{},"exact_concept_policy":None,"approval_record":None,"generated_artifact_references":{},"workflow_history":[]}
        self.save(state); return state
    def get(self, sid: str) -> dict[str, Any]:
        row=self.db.execute("SELECT state,expires FROM sessions WHERE id=?",(sid,)).fetchone()
        if not row or row[1] < time.time(): raise WorkflowError("session_not_found_or_expired")
        return json.loads(row[0])
    def save(self, state: dict[str, Any]) -> None:
        state["workflow_history"].append({"at":int(time.time()),"stage":state["current_stage"]})
        self.db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)",(state["session_id"],json.dumps(state),int(time.time())+self.ttl)); self.db.commit()
    @property
    def ttl(self): return self._ttl

    def purge_expired(self) -> list[str]:
        now=int(time.time())
        rows=self.db.execute("SELECT id FROM sessions WHERE expires < ?",(now,)).fetchall()
        self.db.execute("DELETE FROM sessions WHERE expires < ?",(now,)); self.db.commit()
        return [row[0] for row in rows]

class ACP:
    def __init__(self, settings: Settings): self.base=settings.acp_url.rstrip("/"); self.timeout=settings.timeout
    async def post(self, flow: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try: response=await client.post(f"{self.base}/flows/{flow}",json=payload)
            except httpx.HTTPError as e: raise WorkflowError(f"acp_unavailable:{type(e).__name__}") from e
        if response.status_code >= 500: raise WorkflowError("acp_error")
        try: return response.json()
        except ValueError as e: raise WorkflowError("acp_non_json_response") from e
    def resolve_trusted_url(self, review_url: str) -> httpx.URL:
        """Resolve ACP review links without allowing a leading slash to drop ACP's path prefix."""
        base = httpx.URL(self.base)
        supplied = httpx.URL(review_url)
        if supplied.is_absolute_url:
            url = supplied
        elif review_url.startswith("/"):
            url = base.copy_with(path=base.path.rstrip("/") + supplied.path)
            if supplied.query:
                url = url.copy_with(query=supplied.query)
        else:
            url = httpx.URL(str(base) + "/").join(review_url)
        prefix = base.path.rstrip("/")
        if url.host != base.host or url.scheme != base.scheme or (prefix and not str(url.path).startswith(prefix + "/")):
            raise WorkflowError("untrusted_review_url")
        return url
    async def get_trusted(self, review_url: str) -> bytes:
        url = self.resolve_trusted_url(review_url)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client: response=await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise WorkflowError("review_package_unavailable_or_expired") from e
        return response.content

class Controller:
    def __init__(self, settings: Settings, on_expired: Callable[[list[str]], None] | None = None): self.settings=settings; self.store=Store(settings.db_path, settings.session_ttl); self.acp=ACP(settings); self.on_expired=on_expired; settings.artifact_root.mkdir(parents=True,exist_ok=True)
    def purge_expired(self) -> int:
        removed=self.store.purge_expired(); root=self.settings.artifact_root.resolve()
        for sid in removed:
            candidate=(root / sid).resolve()
            if candidate.parent == root and candidate.is_dir(): shutil.rmtree(candidate)
        if removed and self.on_expired: self.on_expired(removed)
        return len(removed)
    def create(self):
        self.purge_expired(); return self.store.create()
    def state(self,sid): return self.store.get(sid)
    def _save(self,s): self.store.save(s); return s
    @staticmethod
    def _invalidate_downstream(s):
        """Remove active decisions derived from an upstream clinical change."""
        s["selected_phenotypes"]={}
        s["recommendation_results"]={}
        s["confirmed_scope"]=None
        s["concept_review_id"]=None
        s["review_artifact_references"]={}
        s["exact_concept_policy"]=None
        s["approval_record"]=None
        s["generated_artifact_references"]={}
        s.pop("make_computable",None)
        s.pop("improvement_advice",None)
    @staticmethod
    def _invalidate_computable(s):
        s["confirmed_scope"]=None
        s["concept_review_id"]=None
        s["review_artifact_references"]={}
        s["exact_concept_policy"]=None
        s["approval_record"]=None
        s["generated_artifact_references"]={}
        s.pop("make_computable",None)
        s.pop("improvement_advice",None)
    async def capture_intent(self,sid: str, study_intent: str, workflow_type="comparative"):
        s=self.state(sid); self._invalidate_downstream(s)
        result=await self.acp.post("phenotype_intent_split",{"study_intent":study_intent})
        s.update(study_intent=study_intent,workflow_type=workflow_type,current_stage="intent",intent_proposal=result,confirmed_role_statements={},active_role=None)
        return self._save(s)
    def confirm_roles(self,sid: str, statements: dict[str,str]):
        if not statements or any(not v.strip() for v in statements.values()): raise WorkflowError("role_statements_required")
        s=self.state(sid); self._invalidate_downstream(s)
        s["confirmed_role_statements"]=statements; s["active_role"]=next(iter(statements)); s["current_stage"]="recommendations"
        return self._save(s)
    @staticmethod
    def _acp_workflow_type(workflow_type: str) -> str:
        """Map concise browser labels to the ACP flow's established vocabulary."""
        return {"comparative": "cohort_methods", "incidence": "incidence"}.get(workflow_type, workflow_type)

    async def recommend(self,sid: str, role: str, offset=0):
        s=self.state(sid); statement=s["confirmed_role_statements"].get(role)
        if not statement: raise WorkflowError("unknown_role")
        result=await self.acp.post("phenotype_recommendation",{"study_intent":statement,"recommendation_role":role,"workflow_type":self._acp_workflow_type(s["workflow_type"]),"top_k":20,"max_results":5,"candidate_limit":5,"candidate_offset":offset})
        s["recommendation_results"][role]=result; s["active_role"]=role
        return self._save(s)
    def select_phenotype(self,sid: str, role: str, phenotype: dict[str,Any]):
        s=self.state(sid)
        if role not in s.get("confirmed_role_statements",{}): raise WorkflowError("unknown_role")
        source_id=str(phenotype.get("source_id") or phenotype.get("phenotype_id") or "").strip(); provider=str(phenotype.get("source_provider") or phenotype.get("provider") or "").strip()
        if not source_id or (":" not in source_id and not provider): raise WorkflowError("namespaced_source_id_required")
        self._invalidate_computable(s); s["selected_phenotypes"].pop(role,None)
        selected=dict(phenotype); selected["source_id"]=source_id if ":" in source_id else f"{provider}:{source_id}"
        s["selected_phenotypes"][role]=selected; s["active_role"]=role; s["current_stage"]="recommendations"
        circe=selected.get("circe_json") or selected.get("circe")
        if circe is not None:
            root=self.settings.artifact_root/sid; root.mkdir(mode=0o700,exist_ok=True); (root/"circe.json").write_text(circe if isinstance(circe,str) else json.dumps(circe,indent=2)); s["generated_artifact_references"]["selected_circe"]="circe.json"
        return self._save(s)
    async def retrieve_selected_definition(self,sid: str,role: str):
        s=self.state(sid); selected=s.get("selected_phenotypes",{}).get(role)
        if not selected: raise WorkflowError("selected_phenotype_required")
        result=await self.acp.post("phenotype_definition",{"phenotype_id":selected["source_id"],"allow_make_computable":False,"recommendation_context":{"recommendation_role":role,"workflow_type":self._acp_workflow_type(s.get("workflow_type", ""))}})
        selected["definition_result"]=result
        if result.get("status")=="ok" and result.get("circe_json") is not None:
            root=self.settings.artifact_root/sid; root.mkdir(mode=0o700,exist_ok=True)
            circe_name=f"selected_{role}_circe.json"; (root/circe_name).write_text(result["circe_json"] if isinstance(result["circe_json"],str) else json.dumps(result["circe_json"],indent=2))
            s["generated_artifact_references"][f"selected_circe_{role}"]=circe_name
            capr=(result.get("conversion") or {}).get("capr_code")
            if capr:
                capr_name=f"selected_{role}_capr.R"; (root/capr_name).write_text(capr); s["generated_artifact_references"][f"selected_capr_{role}"]=capr_name
        return self._save(s)
    async def request_scope(self,sid: str,narrative: str,role: str=""):
        s=self.state(sid)
        if role:
            if role not in s.get("confirmed_role_statements",{}): raise WorkflowError("unknown_role")
            s["active_role"]=role
        self._invalidate_computable(s)
        if role: s["active_role"]=role
        result=await self.acp.post("phenotype_make_computable",{"narrative_statement":narrative,"confirmed_scope":False,"scope_draft":True,"concept_review_mode":"required","candidate_limit":20,"concept_sets":[]})
        s["make_computable"]={"narrative":narrative,"clarification":result}; s["current_stage"]="scope"; return self._save(s)
    async def confirm_scope(self,sid: str, scope: dict[str,Any], candidate_limit=20):
        s=self.state(sid); narrative=s.get("make_computable",{}).get("narrative")
        if not narrative: raise WorkflowError("narrative_required")
        result=await self.acp.post("phenotype_make_computable",{"narrative_statement":narrative,"confirmed_scope":True,"scope":scope,"concept_review_mode":"required","review_delivery":"session","candidate_limit":min(max(int(candidate_limit),1),500),"concept_sets":[]})
        s["confirmed_scope"]=scope; s["review_artifact_references"]={}; s["exact_concept_policy"]=None; s["approval_record"]=None; s["generated_artifact_references"]={}; s["concept_review_id"]=result.get("review_id"); s["make_computable"]["review"]=result; s["current_stage"]="review" if result.get("status")=="needs_concept_review" else "scope"; return self._save(s)
    async def freeze_review_package(self,sid: str):
        s=self.state(sid); review=s.get("make_computable",{}).get("review",{}); urls=review.get("review_urls") or {}
        if not s.get("concept_review_id") or not urls.get("candidates_csv") or not urls.get("manifest"): raise WorkflowError("review_session_package_unavailable")
        csv, manifest=await self.acp.get_trusted(urls["candidates_csv"]), await self.acp.get_trusted(urls["manifest"])
        try: manifest_json=json.loads(manifest)
        except ValueError as e: raise WorkflowError("invalid_review_manifest") from e
        if manifest_json.get("review_id") != s["concept_review_id"]: raise WorkflowError("review_manifest_mismatch")
        root=self.settings.artifact_root/sid; root.mkdir(mode=0o700,exist_ok=True); (root/"review.csv").write_bytes(csv); (root/"manifest.json").write_bytes(manifest)
        s["review_artifact_references"]={"csv":"review.csv","manifest":"manifest.json","review_id":s["concept_review_id"]}; return self._save(s)
    def ingest_review_csv(self,sid: str, csv_bytes: bytes):
        if len(csv_bytes)>5*1024*1024: raise WorkflowError("review_upload_too_large")
        s=self.state(sid); root=self.settings.artifact_root/sid; manifest_path=root/"manifest.json"
        if not manifest_path.exists(): raise WorkflowError("frozen_review_package_required")
        manifest=json.loads(manifest_path.read_text());
        if manifest.get("review_id") != s.get("concept_review_id"): raise WorkflowError("review_manifest_mismatch")
        parsed=parse_review_csv(csv_bytes,manifest)
        (root/"approval.json").write_bytes(parsed.pop("approval_json")); s["exact_concept_policy"]={k:v for k,v in parsed.items() if k!="approval_json"}; s["approval_record"]=None; s["current_stage"]="review"; return self._save(s)
    def ingest_atlas_set(self,sid: str, payload: dict[str,Any], name="", domain=""):
        s=self.state(sid); parsed=normalize_atlas_concept_set(payload,name,domain); encoded=json.dumps({"concept_sets":parsed["concept_sets"]},sort_keys=True).encode(); s["exact_concept_policy"]={"concept_sets":parsed["concept_sets"],"source":"atlas_json","policy_sha256":hashlib.sha256(encoded).hexdigest()}; s["approval_record"]=None; s["current_stage"]="review"; return self._save(s)
    def approve_policy(self,sid: str, digest: str):
        s=self.state(sid); policy=s.get("exact_concept_policy") or {}
        if not policy.get("concept_sets") or not secrets.compare_digest(digest,policy.get("policy_sha256","")): raise WorkflowError("exact_policy_digest_required")
        s["approval_record"]={"policy_sha256":digest,"approved_at":int(time.time()),"scope_sha256":hashlib.sha256(json.dumps(s["confirmed_scope"],sort_keys=True).encode()).hexdigest(),"review_id":s.get("concept_review_id")}; s["current_stage"]="approved"; return self._save(s)
    async def emit(self,sid: str):
        s=self.state(sid); approval=s.get("approval_record"); policy=s.get("exact_concept_policy") or {}
        if not approval or approval.get("policy_sha256") != policy.get("policy_sha256"): raise WorkflowError("exact_policy_approval_required")
        result=await self.acp.post("phenotype_make_computable",{"narrative_statement":s["make_computable"]["narrative"],"confirmed_scope":True,"scope":s["confirmed_scope"],"concept_review_mode":"provided_only","concept_sets":policy["concept_sets"]})
        if result.get("status") == "ok":
            root=self.settings.artifact_root/sid; root.mkdir(mode=0o700,exist_ok=True)
            for name, value in {"capr.R":result.get("capr",{}).get("source") or result.get("capr_code"),"circe.json":result.get("circe_json") or result.get("circe")}.items():
                if value is not None: (root/name).write_text(value if isinstance(value,str) else json.dumps(value,indent=2))
            s["generated_artifact_references"]={"result":result,"capr":"capr.R","circe":"circe.json"}; s["current_stage"]="complete"
        return self._save(s)
    async def contextual_guidance(self,sid: str, user_prompt: str):
        s=self.state(sid)
        step={"intent":"intent_split","recommendations":(str(s.get("active_role") or "target") + "_selection"),"scope":"phenotype_review","review":"phenotype_review","approved":"phenotype_review","complete":"workflow_summary"}.get(s["current_stage"],"phenotype_review")
        result=await self.acp.post("workflow_context_dialogue",{"user_prompt":user_prompt,"study_intent":s.get("study_intent", ""),"workflow_type":s.get("workflow_type", ""),"current_step":step,"current_role":s.get("active_role") or "","current_context":{"confirmed_role_statements":s.get("confirmed_role_statements",{}),"selected_phenotypes":s.get("selected_phenotypes",{}),"current_stage":s.get("current_stage")}})
        s["last_contextual_guidance"]=result; return self._save(s)
    async def improvement_advice(self,sid: str, phenotype: dict[str,Any]):
        s=self.state(sid); cohort=dict(phenotype); cohort.setdefault("name", cohort.get("phenotype_name") or cohort.get("source_id") or "Selected phenotype"); result=await self.acp.post("phenotype_improvements",{"protocol_text":s.get("study_intent") or "Phenotype improvement advisory request.","cohorts":[cohort],"characterization_previews":[]})
        s["improvement_advice"]={"advisory":True,"result":result,"atlas3":"https://atlas.napdi.org/Atlas3"}; return self._save(s)
