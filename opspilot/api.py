import os
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,Query,Request as HTTPRequest
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from threading import Lock
from .tool_agent import ToolAgent,ModelConfig
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from typing import Literal
from .agent import Agent
from .ingest import ROOT

@asynccontextmanager
async def lifespan(app):
    app.state.agent=Agent(); app.state.model_config=ModelConfig.environment(); app.state.call_lock=Lock(); app.state.latest=None; yield
app=FastAPI(title='OpsPilot',version='0.2.0',lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    return JSONResponse(status_code=422,content={'detail':[{'loc':e['loc'],'msg':e['msg'],'type':e['type']} for e in exc.errors()]})
@app.middleware('http')
async def origin_guard(request,call_next):
    if request.method in ('POST','PUT','DELETE'):
        origin=request.headers.get('origin')
        if origin and origin!='http://'+request.headers.get('host',''):
            return JSONResponse(status_code=403,content={'detail':'Cross-origin mutation rejected'})
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response
class Request(BaseModel):
    question:str=Field(min_length=3,max_length=1200)
    mode:Literal['keyword','vector','hybrid','agent']='hybrid'
    rerank:bool=False
    llm:bool=False
@app.get('/')
def home(): return FileResponse(ROOT/'web/index.html')
@app.get('/health')
def health(): return {'status':'ok','rows':len(app.state.agent.logs.items),'embedding':app.state.agent.logs.embedding,'agent_version':'0.2.0','llm_configured':app.state.model_config.public()['configured']}
@app.get('/logs')
def logs(contains:str=Query('',max_length=200),level:Literal['error','notice','warn','info','debug']|None=None,limit:int=Query(20,ge=1,le=100),start:int=Query(1,ge=1,le=2000),end:int=Query(2000,ge=1,le=2000)):
    try: return app.state.agent.tools.search(contains,level,limit,start,end)
    except ValueError as e: raise HTTPException(422,str(e))
@app.get('/stats')
def stats(): return app.state.agent.tools.stats()
@app.post('/diagnose')
def diagnose(req:Request):
    if req.mode=='agent':
        if not app.state.call_lock.acquire(blocking=False): raise HTTPException(429,'An investigation is already running')
        try:
            result=ToolAgent(app.state.agent,app.state.model_config).run(req.question)
            app.state.latest=result
            return result
        finally: app.state.call_lock.release()
    if req.llm and not app.state.model_config.public()['configured']: raise HTTPException(409,'Server API mode is not enabled')
    return app.state.agent.diagnose(req.question,req.mode,req.rerank,req.llm,config=app.state.model_config)

class LocalConfig(BaseModel):
    api_key:str=Field(min_length=8,max_length=256)
    model:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_.:/-]+$')
@app.get('/setup')
def setup(): return FileResponse(ROOT/'web/setup.html')
@app.get('/config')
def config_status(): return app.state.model_config.public()
@app.post('/config')
def configure(req:LocalConfig):
    # The browser cannot configure arbitrary destinations or retrieve the key.
    app.state.model_config=ModelConfig(req.api_key,req.model,'https://api.deepseek.com')
    return app.state.model_config.public()
@app.delete('/config')
def clear_config():
    app.state.model_config=ModelConfig('','','https://api.deepseek.com');return {'configured':False}
@app.get('/runs/latest')
def latest_run(): return app.state.latest or {'status':'no_run'}
