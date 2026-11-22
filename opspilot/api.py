import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import Agent
from .ingest import ROOT
from .tool_agent import ModelConfig, ToolAgent


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = Agent()
    app.state.model_config = ModelConfig.environment()
    app.state.call_semaphore = asyncio.Semaphore(4)
    app.state.latest = None
    yield


app = FastAPI(title="OpsPilot", version="0.3.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]},
    )


@app.middleware("http")
async def origin_guard(request, call_next):
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin != "http://" + request.headers.get("host", ""):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin mutation rejected"})
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


class Request(BaseModel):
    question: str = Field(min_length=3, max_length=1200)
    mode: Literal["keyword", "vector", "hybrid", "agent"] = "hybrid"
    rerank: bool = False
    rerank_mode: Literal["lexical", "cross-encoder"] = "lexical"
    llm: bool = False


@app.get("/")
def home():
    return FileResponse(ROOT / "web/index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "dataset": app.state.agent.dataset,
        "rows": len(app.state.agent.logs.items),
        "max_line": app.state.agent.tools.max_line,
        "embedding": app.state.agent.logs.embedding,
        "index_loaded_from_disk": app.state.agent.logs.index_loaded_from_disk,
        "index_build_ms": app.state.agent.logs.index_build_ms,
        "agent_version": "0.3.0",
        "llm_configured": app.state.model_config.public()["configured"],
    }


@app.get("/logs")
def logs(
    contains: str = Query("", max_length=200),
    level: Literal["error", "notice", "warn", "info", "debug"] | None = None,
    limit: int = Query(20, ge=1, le=100),
    start: int = Query(1, ge=1),
    end: int | None = Query(None, ge=1),
):
    try:
        return app.state.agent.tools.search(contains, level, limit, start, end)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/stats")
def stats():
    return app.state.agent.tools.stats()


@app.post("/diagnose")
async def diagnose(req: Request):
    if req.mode == "agent":
        if app.state.call_semaphore.locked():
            raise HTTPException(429, "Too many concurrent investigations running")
        async with app.state.call_semaphore:
            result = await ToolAgent(app.state.agent, app.state.model_config).arun(req.question)
            app.state.latest = result
            return result
    if req.llm and not app.state.model_config.public()["configured"]:
        raise HTTPException(409, "Server API mode is not enabled")
    return app.state.agent.diagnose(
        req.question,
        req.mode,
        req.rerank,
        req.llm,
        config=app.state.model_config,
        rerank_mode=req.rerank_mode,
    )


class LocalConfig(BaseModel):
    api_key: str = Field(min_length=8, max_length=256)
    model: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:/-]+$")
    base_url: str = Field(default="https://api.deepseek.com", max_length=200)


@app.get("/setup")
def setup():
    return FileResponse(ROOT / "web/setup.html")


@app.get("/config")
def config_status():
    return app.state.model_config.public()


@app.post("/config")
def configure(req: LocalConfig):
    # Only allow official DeepSeek HTTPS or local loopback security gateway
    candidate = ModelConfig(req.api_key, req.model, req.base_url)
    try:
        candidate.validate()
    except ValueError as e:
        raise HTTPException(422, str(e))
    app.state.model_config = candidate
    return app.state.model_config.public()


@app.delete("/config")
def clear_config():
    app.state.model_config = ModelConfig("", "", "https://api.deepseek.com")
    return {"configured": False}


@app.get("/runs/latest")
def latest_run():
    return app.state.latest or {"status": "no_run"}
