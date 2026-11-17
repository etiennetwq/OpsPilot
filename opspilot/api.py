import os
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,Query
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from typing import Literal
from .agent import Agent
from .ingest import ROOT

@asynccontextmanager
async def lifespan(app):
    app.state.agent=Agent(); yield
app=FastAPI(title='OpsPilot',version='0.1.0',lifespan=lifespan)
class Request(BaseModel):
    question:str=Field(min_length=3,max_length=1200)
    mode:Literal['keyword','vector','hybrid']='hybrid'
    rerank:bool=False
    llm:bool=False
@app.get('/')
def home(): return FileResponse(ROOT/'web/index.html')
@app.get('/health')
def health(): return {'status':'ok','rows':len(app.state.agent.logs.items),'embedding':app.state.agent.logs.embedding}
@app.get('/logs')
def logs(contains:str=Query('',max_length=200),level:Literal['error','notice','warn','info','debug']|None=None,limit:int=Query(20,ge=1,le=100),start:int=Query(1,ge=1,le=2000),end:int=Query(2000,ge=1,le=2000)):
    try: return app.state.agent.tools.search(contains,level,limit,start,end)
    except ValueError as e: raise HTTPException(422,str(e))
@app.get('/stats')
def stats(): return app.state.agent.tools.stats()
@app.post('/diagnose')
def diagnose(req:Request):
    if req.llm and os.getenv('OPSPILOT_LLM','offline')!='api': raise HTTPException(409,'Server API mode is not enabled')
    return app.state.agent.diagnose(req.question,req.mode,req.rerank,req.llm)
