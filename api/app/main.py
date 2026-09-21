from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.observability import setup_observability


@asynccontextmanager
async def lifespan(_: FastAPI):
    print(f"hello world (observability: {setup_observability(settings)})")
    yield


app = FastAPI(title="medical-necessity", lifespan=lifespan)


@app.get("/")
def hello() -> dict[str, str]:
    return {"message": "hello world"}
