from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.job_manager import JobManager


app = FastAPI(title="书籍浓缩器", version="1.0.0")
manager = JobManager()


class CondenseRequest(BaseModel):
    mode: str = Field(pattern="^(one|ten|all|failed|selected)$")
    chapter_ids: list[str] = []


class ExportRequest(BaseModel):
    chapter_ids: list[str] = []


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/models")
def models() -> dict:
    stored_credential = manager.credential_store.load()
    return {
        "default": config.DEFAULT_MODEL,
        "models": config.SUPPORTED_MODELS,
        "default_region": config.DEFAULT_REGION,
        "regions": [
            {"id": region, "label": label}
            for region, label in config.REGION_LABELS.items()
        ],
        "has_api_key": bool(config.MINIMAX_API_KEY or stored_credential or config.MOCK_AI),
        "stored_region": stored_credential.region if stored_credential else "",
    }


@app.post("/api/jobs")
def create_job(
    file: UploadFile = File(...),
    model: str = Form(config.DEFAULT_MODEL),
    api_key: str = Form(""),
    region: str = Form(config.DEFAULT_REGION),
) -> dict:
    try:
        job = manager.create_job(file, model, api_key=api_key, region=region)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_id": job.id, "status": job.status.value}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    snapshot = manager.snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return snapshot


@app.get("/api/jobs/{job_id}/chapters/{chapter_id}")
def get_chapter(job_id: str, chapter_id: str) -> dict:
    chapter = manager.get_chapter_content(job_id, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在。")
    return chapter


@app.post("/api/jobs/{job_id}/condense")
def condense(job_id: str, request: CondenseRequest) -> dict:
    try:
        selected = manager.condense(job_id, request.mode, request.chapter_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_id": job_id, "selected_chapter_ids": selected}


@app.post("/api/jobs/{job_id}/stop")
def stop_condense(job_id: str) -> dict:
    try:
        stopped = manager.stop_condense(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job_id, "stopped": stopped}


@app.post("/api/jobs/{job_id}/exports")
def create_export(job_id: str, request: ExportRequest) -> dict:
    try:
        path = manager.export_epub(job_id, request.chapter_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "download_url": f"/api/jobs/{job_id}/exports/{path.stem}/download",
        "filename": path.name,
    }


@app.get("/api/jobs/{job_id}/exports/{export_id}/download")
def download_export(job_id: str, export_id: str) -> FileResponse:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    export_dir = manager.storage_dir / "jobs" / job_id / "exports"
    matches = list(export_dir.glob(f"{export_id}.epub"))
    if not matches:
        raise HTTPException(status_code=404, detail="EPUB 文件不存在。")
    path = matches[0]
    return FileResponse(path, media_type="application/epub+zip", filename=path.name)


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str) -> FileResponse:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    try:
        path = manager.export_epub(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="EPUB 文件不存在。")
    filename = path.name
    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=filename,
    )


static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
