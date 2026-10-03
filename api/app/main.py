import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.graph.criteria import load_criteria
from app.llm.client import build_client
from app.mcp.client import McpConnection, connect
from app.observability import setup_observability
from app.routes.cases import Runtime, router as cases_router

CRITERIA_FILE = "adalimumab_ra.yaml"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One MCP session and one LLM client for the life of the process."""
    tracing = setup_observability(settings)
    mcp = McpConnection(lambda: connect(settings.mcp_url))
    supervisor = asyncio.create_task(mcp.run())
    try:
        llm, llm_error = build_client(settings), None
    except Exception as err:  # e.g. no API key: the API still serves, cases fail with the reason
        llm, llm_error = None, f"no LLM client for {settings.llm_provider}: {type(err).__name__}: {err}"
    app.state.runtime = Runtime(session=mcp, llm=llm, llm_error=llm_error,
                                criteria=load_criteria(settings.criteria_dir / CRITERIA_FILE),
                                as_of=settings.as_of)
    app.state.info = {"status": "ok", "mcp": settings.mcp_url or "in-process",
                      "llm": None if llm is None else settings.llm_provider, "traces": tracing,
                      "as_of": settings.as_of.isoformat()}
    try:
        yield
    finally:
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)


app = FastAPI(title="medical-necessity", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                   allow_headers=["content-type"])
app.include_router(cases_router)


@app.get("/")
def health() -> dict:
    return app.state.info
