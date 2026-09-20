from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_: FastAPI):
    print("hello world")
    yield


app = FastAPI(title="medical-necessity", lifespan=lifespan)


@app.get("/")
def hello() -> dict[str, str]:
    return {"message": "hello world"}
