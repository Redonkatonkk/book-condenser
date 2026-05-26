from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.credentials import MiniMaxCredential
from app.job_manager import JobManager
from app.minimax_client import MiniMaxAuthError, MiniMaxError
from app.user_store import (
    SESSION_TTL_SECONDS,
    AccountLockedError,
    InvalidPasswordError,
    UnknownUserError,
    User,
)


app = FastAPI(title="书籍浓缩器", version="1.0.0")
manager = JobManager()
EXPORT_ID_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]+$")
SESSION_COOKIE = "book_condenser_session"


class CondenseRequest(BaseModel):
    mode: str = Field(pattern="^(one|ten|all|failed|selected)$")
    chapter_ids: list[str] = Field(default_factory=list)


class ExportRequest(BaseModel):
    chapter_ids: list[str] = Field(default_factory=list)


class AuthRequest(BaseModel):
    email: str
    password: str


class ApiKeyRequest(BaseModel):
    api_key: str = ""
    region: str = config.DEFAULT_REGION


def optional_user(request: Request) -> Optional[User]:
    return manager.user_store.get_user_by_session(request.cookies.get(SESSION_COOKIE, ""))


def require_user(user: Optional[User] = Depends(optional_user)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="请先登录。")
    return user


def user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "created_at": user.created_at,
        "has_api_key": user.has_api_key,
        "region": user.region,
    }


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )


def user_id(user: Optional[User]) -> str:
    return user.id if user else ""


def ensure_job_access(job_id: str, user: Optional[User]) -> None:
    if not manager.can_access(job_id, user_id(user)):
        raise HTTPException(status_code=404, detail="任务不存在。")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user: Optional[User] = Depends(optional_user)) -> dict:
    return {
        "authenticated": bool(user),
        "user": user_payload(user) if user else None,
    }


@app.post("/api/auth/register")
def register(request: AuthRequest, response: Response) -> dict:
    try:
        user = manager.user_store.create_user(request.email, request.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = manager.user_store.create_session(user.id)
    set_session_cookie(response, token)
    return {"user": user_payload(user)}


@app.post("/api/auth/login")
def login(request: AuthRequest, response: Response) -> dict:
    try:
        user = manager.user_store.authenticate(request.email, request.password)
    except UnknownUserError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidPasswordError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AccountLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = manager.user_store.create_session(user.id)
    set_session_cookie(response, token)
    return {"user": user_payload(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    manager.user_store.delete_session(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/account/api-key")
def get_account_api_key(user: User = Depends(require_user)) -> dict:
    current = manager.user_store.get_user(user.id) or user
    return {
        "has_api_key": current.has_api_key,
        "region": current.region,
    }


@app.put("/api/account/api-key")
def save_account_api_key(request: ApiKeyRequest, user: User = Depends(require_user)) -> dict:
    try:
        if request.api_key.strip():
            region = request.region.strip().lower()
            if region not in config.REGION_ENDPOINTS:
                region = config.DEFAULT_REGION
            credential = MiniMaxCredential(api_key=request.api_key.strip(), region=region)
            manager.ai_client.validate_api_key(
                credential.api_key,
                api_url=credential.resolved_api_url,
            )
            manager.user_store.save_api_key(user.id, credential.api_key, credential.region)
        else:
            manager.user_store.clear_api_key(user.id)
    except (MiniMaxAuthError, MiniMaxError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    current = manager.user_store.get_user(user.id) or user
    return {
        "has_api_key": current.has_api_key,
        "region": current.region,
    }


@app.get("/api/models")
def models(user: Optional[User] = Depends(optional_user)) -> dict:
    stored_credential = manager.credential_store.load()
    user_credential = manager.user_store.get_credential(user.id) if user else None
    region = user_credential.region if user_credential else ""
    if not region and stored_credential:
        region = stored_credential.region
    return {
        "default": config.DEFAULT_MODEL,
        "models": config.SUPPORTED_MODELS,
        "default_region": config.DEFAULT_REGION,
        "regions": [
            {"id": region, "label": label}
            for region, label in config.REGION_LABELS.items()
        ],
        "has_api_key": bool(
            config.MINIMAX_API_KEY or user_credential or stored_credential or config.MOCK_AI
        ),
        "user_has_api_key": bool(user_credential),
        "stored_region": region,
    }


@app.post("/api/jobs")
def create_job(
    file: UploadFile = File(...),
    model: str = Form(config.DEFAULT_MODEL),
    api_key: str = Form(""),
    region: str = Form(config.DEFAULT_REGION),
    user: Optional[User] = Depends(optional_user),
) -> dict:
    try:
        job = manager.create_job(
            file,
            model,
            api_key=api_key,
            region=region,
            user_id=user_id(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_id": job.id, "status": job.status.value}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user: Optional[User] = Depends(optional_user)) -> dict:
    ensure_job_access(job_id, user)
    snapshot = manager.snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return snapshot


@app.get("/api/jobs/{job_id}/chapters/{chapter_id}")
def get_chapter(
    job_id: str,
    chapter_id: str,
    user: Optional[User] = Depends(optional_user),
) -> dict:
    ensure_job_access(job_id, user)
    chapter = manager.get_chapter_content(job_id, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在。")
    return chapter


@app.post("/api/jobs/{job_id}/condense")
def condense(
    job_id: str,
    request: CondenseRequest,
    user: Optional[User] = Depends(optional_user),
) -> dict:
    ensure_job_access(job_id, user)
    try:
        selected = manager.condense(job_id, request.mode, request.chapter_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_id": job_id, "selected_chapter_ids": selected}


@app.post("/api/jobs/{job_id}/stop")
def stop_condense(job_id: str, user: Optional[User] = Depends(optional_user)) -> dict:
    ensure_job_access(job_id, user)
    try:
        stopped = manager.stop_condense(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job_id, "stopped": stopped}


@app.post("/api/jobs/{job_id}/exports")
def create_export(
    job_id: str,
    request: ExportRequest,
    user: Optional[User] = Depends(optional_user),
) -> dict:
    ensure_job_access(job_id, user)
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
def download_export(
    job_id: str,
    export_id: str,
    user: Optional[User] = Depends(optional_user),
) -> FileResponse:
    ensure_job_access(job_id, user)
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if not EXPORT_ID_RE.fullmatch(export_id) or export_id in {".", ".."}:
        raise HTTPException(status_code=404, detail="EPUB 文件不存在。")
    export_dir = manager.storage_dir / "jobs" / job_id / "exports"
    path = export_dir / f"{export_id}.epub"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="EPUB 文件不存在。")
    return FileResponse(path, media_type="application/epub+zip", filename=path.name)


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str, user: Optional[User] = Depends(optional_user)) -> FileResponse:
    ensure_job_access(job_id, user)
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


@app.get("/api/me/jobs")
def list_my_jobs(user: User = Depends(require_user)) -> dict:
    return {"jobs": manager.list_user_jobs(user.id)}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, user: User = Depends(require_user)) -> dict:
    job = manager.get_job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return {"deleted": manager.delete_job(job_id)}


static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
