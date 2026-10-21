from __future__ import annotations
import json, os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .controller import Controller, Settings, WorkflowError
from .chatkit_server import ChatRequestContext, SQLiteChatKitStore, StudyChatKitServer

ROOT=Path(__file__).resolve().parents[1]
settings=Settings(os.getenv("STUDY_AGENT_URL","http://127.0.0.1:8765"),Path(os.getenv("SESSION_DB",ROOT/"var/study-chat.sqlite3")),Path(os.getenv("ARTIFACT_ROOT",ROOT/"var/artifacts")),float(os.getenv("ACP_TIMEOUT_SECONDS","45")),session_ttl=max(1,int(os.getenv("SESSION_TTL_HOURS","24")))*3600)
chatkit_store=SQLiteChatKitStore(settings.db_path)
controller=Controller(settings, on_expired=chatkit_store.purge_sessions)
chatkit_server=StudyChatKitServer(chatkit_store,controller,os.getenv("OPENAI_MODEL","gpt-4.1-mini"))
app=FastAPI(title="OHDSI Study Agent — Interactive Demonstration")
app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
def fail(e): raise HTTPException(status_code=400,detail=str(e))
@app.exception_handler(WorkflowError)
async def workflow_error(_,exc): return __import__("fastapi").responses.JSONResponse({"detail":str(exc)},status_code=409)
class Intent(BaseModel): study_intent:str=Field(min_length=3,max_length=4000); workflow_type:str="comparative"
class Roles(BaseModel): statements:dict[str,str]
class Scope(BaseModel): scope:dict; candidate_limit:int=20
class Select(BaseModel): role:str; phenotype:dict
class Approval(BaseModel): digest:str
class Atlas(BaseModel): payload:dict; name:str=""; domain:str=""
@app.get("/",response_class=HTMLResponse)
async def index(): return (ROOT/"web/index.html").read_text()
@app.get("/api/config")
async def config(): return {"chatkit_domain_key":os.getenv("CHATKIT_DOMAIN_KEY","")}
@app.post("/chatkit")
async def chatkit(request: Request):
    session_id=request.headers.get("X-Study-Session","")
    if not session_id: raise HTTPException(401,"study session is required")
    controller.state(session_id)
    result=await chatkit_server.process(await request.body(),ChatRequestContext(session_id))
    if hasattr(result,"json_events"): return StreamingResponse(result,media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
    return Response(content=result.json,media_type="application/json")
@app.post("/api/sessions")
async def create(): return controller.create()
@app.get("/api/sessions/{sid}")
async def state(sid:str): return controller.state(sid)
@app.post("/api/sessions/{sid}/intent")
async def intent(sid:str,body:Intent): return await controller.capture_intent(sid,body.study_intent,body.workflow_type)
@app.post("/api/sessions/{sid}/roles")
async def roles(sid:str,body:Roles): return controller.confirm_roles(sid,body.statements)
@app.post("/api/sessions/{sid}/recommendations/{role}")
async def recommend(sid:str,role:str): return await controller.recommend(sid,role)
@app.post("/api/sessions/{sid}/select")
async def select(sid:str,body:Select): return controller.select_phenotype(sid,body.role,body.phenotype)
@app.post("/api/sessions/{sid}/make-computable")
async def clarify(sid:str,body:dict): return await controller.request_scope(sid,str(body.get("narrative","")),str(body.get("role", "")))
@app.post("/api/sessions/{sid}/scope")
async def scope(sid:str,body:Scope): return await controller.confirm_scope(sid,body.scope,body.candidate_limit)
@app.post("/api/sessions/{sid}/review/freeze")
async def freeze(sid:str): return await controller.freeze_review_package(sid)
@app.post("/api/sessions/{sid}/review/upload")
async def upload(sid:str,file:UploadFile): return controller.ingest_review_csv(sid,await file.read())
@app.post("/api/sessions/{sid}/review/atlas")
async def atlas(sid:str,body:Atlas): return controller.ingest_atlas_set(sid,body.payload,body.name,body.domain)
@app.post("/api/sessions/{sid}/approve")
async def approve(sid:str,body:Approval): return controller.approve_policy(sid,body.digest)
@app.post("/api/sessions/{sid}/emit")
async def emit(sid:str): return await controller.emit(sid)
@app.post("/api/sessions/{sid}/context")
async def context(sid:str,body:dict): return await controller.contextual_guidance(sid,str(body.get("prompt") or ""))
@app.post("/api/sessions/{sid}/improvement-advice")
async def improvement_advice(sid:str,body:dict):
    phenotype=body.get("phenotype")
    if not isinstance(phenotype,dict) or not phenotype: raise HTTPException(422,"phenotype object is required")
    return await controller.improvement_advice(sid,phenotype)
@app.get("/api/sessions/{sid}/artifacts/{name}")
async def artifact(sid:str,name:str):
    if name not in {"capr.R","circe.json","review.csv","manifest.json","approval.json"}: raise HTTPException(404)
    controller.state(sid)
    path=settings.artifact_root/sid/name
    if not path.exists(): raise HTTPException(404)
    return FileResponse(path,filename=name)
