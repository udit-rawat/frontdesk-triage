"""HTTP layer: the triage API and the page that consumes it."""
import pathlib

from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app import store  # noqa: E402
from app.triage import PRIMARY_MODEL, triage  # noqa: E402

STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="FrontDesk", description="AI request triage assistant", version="1.0")


@app.on_event("startup")
def _startup() -> None:
    store.init()


class TriageIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
    id: str | None = None


class DraftIn(BaseModel):
    draft_reply: str = Field(..., min_length=1, max_length=8000)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "model": PRIMARY_MODEL}


@app.get("/api/requests")
def list_requests() -> list[dict]:
    return store.list_requests()


@app.post("/api/triage")
def run_triage(payload: TriageIn) -> dict:
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "empty request")

    request_id = payload.id
    if request_id:
        if store.get(request_id) is None:
            raise HTTPException(404, "unknown request id")
    else:
        request_id = store.add(text)

    result = triage(text)
    result["triage"] = result["triage"].model_dump()
    store.save_triage(request_id, result)
    return store.get(request_id)


@app.put("/api/requests/{request_id}/draft")
def edit_draft(request_id: str, payload: DraftIn) -> dict:
    record = store.update_draft(request_id, payload.draft_reply.strip())
    if record is None:
        raise HTTPException(404, "request not found or not triaged yet")
    return record


@app.post("/api/reset")
def reset() -> dict:
    store.reset()
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
