from __future__ import annotations
import json, os
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from .controller import Controller, Settings, WorkflowError

ROOT=Path(__file__).resolve().parents[1]
settings=Settings(os.getenv("STUDY_AGENT_URL","http://127.0.0.1:8765"),Path(os.getenv("SESSION_DB",ROOT/"var/study-chat.sqlite3")),Path(os.getenv("ARTIFACT_ROOT",ROOT/"var/artifacts")),float(os.getenv("ACP_TIMEOUT_SECONDS","45")))
controller=Controller(settings)
app=FastAPI(title="OHDSI Study Agent — Interactive Demonstration",root_path=os.getenv("APP_BASE_PATH","/study-chat"))
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
async def clarify(sid:str,body:dict): return await controller.request_scope(sid,str(body.get("narrative","")))
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
@app.get("/api/sessions/{sid}/artifacts/{name}")
async def artifact(sid:str,name:str):
    if name not in {"capr.R","circe.json","review.csv","manifest.json","approval.json"}: raise HTTPException(404)
    path=settings.artifact_root/sid/name
    if not path.exists(): raise HTTPException(404)
    return FileResponse(path,filename=name)
