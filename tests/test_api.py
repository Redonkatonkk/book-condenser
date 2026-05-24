from __future__ import annotations

import io
import time
import math
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app import job_manager as job_manager_module
from app.job_manager import JobManager
from app.main import app, manager
from app.minimax_client import MiniMaxAuthError


def test_upload_poll_preview_and_download(tmp_path: Path) -> None:
    manager.storage_dir = tmp_path
    manager.ai_client.mock_mode = True
    client = TestClient(app)

    sample = (
        "第一章 开端\n"
        "这是一个很长的开端。主人公发现一本书，并决定开始调查。\n\n"
        "第二章 线索\n"
        "主人公进入图书馆，找到关键线索，并确认事件并非偶然。"
    ).encode("utf-8")

    create = client.post(
        "/api/jobs",
        data={"model": "MiniMax-M2.7"},
        files={"file": ("sample.txt", sample, "text/plain")},
    )
    assert create.status_code == 200
    job_id = create.json()["job_id"]

    snapshot = wait_for_status(client, job_id, {"ready", "failed"})
    assert snapshot["status"] == "ready", snapshot
    assert snapshot["download_ready"] is False
    assert len(snapshot["chapters"]) == 2

    start = client.post(f"/api/jobs/{job_id}/condense", json={"mode": "all", "chapter_ids": []})
    assert start.status_code == 200
    snapshot = wait_for_status(client, job_id, {"completed", "failed"})
    assert snapshot["status"] == "completed", snapshot
    assert snapshot["download_ready"] is True
    assert snapshot["progress"] == 100
    assert snapshot["elapsed_seconds"] is not None

    chapter_id = snapshot["chapters"][0]["id"]
    chapter = client.get(f"/api/jobs/{job_id}/chapters/{chapter_id}")
    assert chapter.status_code == 200
    assert chapter.json()["content"]

    download = client.get(f"/api/jobs/{job_id}/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/epub+zip")


def test_job_requires_api_key_when_backend_has_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(job_manager_module.config, "MOCK_AI", False)
    isolated = JobManager(storage_dir=tmp_path, ai_client=DummyClient(api_key=""))
    upload = UploadFile(file=io.BytesIO(b"Chapter 1\nHello."), filename="sample.txt")

    try:
        isolated.create_job(upload, "MiniMax-M2.7")
    except ValueError as exc:
        assert "API Key" in str(exc)
    else:
        raise AssertionError("missing API key should be rejected")


def test_user_supplied_api_key_is_used_and_not_exposed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(job_manager_module.config, "MOCK_AI", False)
    client = DummyClient(api_key="")
    isolated = JobManager(storage_dir=tmp_path, ai_client=client)
    upload = UploadFile(
        file=io.BytesIO("第一章\n这是正文内容。".encode("utf-8")),
        filename="sample.txt",
    )

    job = isolated.create_job(upload, "MiniMax-M2.7", api_key="secret-key")
    snapshot = wait_for_manager_status(isolated, job.id, {"ready", "failed"})

    assert snapshot is not None
    assert snapshot["status"] == "ready", snapshot
    assert client.seen_api_key == "secret-key"
    assert "secret-key" not in str(snapshot)
    stored = isolated.credential_store.load()
    assert stored is not None
    assert stored.api_key == "secret-key"
    assert stored.region == "cn"


def test_invalid_api_key_fails_before_condensing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(job_manager_module.config, "MOCK_AI", False)
    client = RejectingClient(api_key="")
    isolated = JobManager(storage_dir=tmp_path, ai_client=client)
    upload = UploadFile(
        file=io.BytesIO("第一章\n这是正文内容。".encode("utf-8")),
        filename="sample.txt",
    )

    job = isolated.create_job(upload, "MiniMax-M2.7", api_key="bad-key")
    snapshot = wait_for_manager_status(isolated, job.id, {"completed", "failed"})

    assert snapshot is not None
    assert snapshot["status"] == "failed"
    assert "鉴权" in snapshot["error"]
    assert client.condense_calls == 0


def test_invalid_key_clears_stored_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(job_manager_module.config, "MOCK_AI", False)
    client = RejectingClient(api_key="")
    isolated = JobManager(storage_dir=tmp_path, ai_client=client)
    isolated.credential_store.save("old-key", "cn")
    upload = UploadFile(
        file=io.BytesIO("第一章\n这是正文内容。".encode("utf-8")),
        filename="sample.txt",
    )

    job = isolated.create_job(upload, "MiniMax-M2.7")
    wait_for_manager_status(isolated, job.id, {"completed", "failed"})

    assert isolated.credential_store.load() is None


def test_partial_condense_retry_failed_and_selected_export(tmp_path: Path) -> None:
    client_impl = FlakyClient(api_key="server-key")
    isolated = JobManager(storage_dir=tmp_path, ai_client=client_impl)
    upload = UploadFile(
        file=io.BytesIO(
            (
                "第一章 开端\n这是第一章正文。\n\n"
                "第二章 线索\n这是第二章正文。\n\n"
                "第三章 结尾\n这是第三章正文。"
            ).encode("utf-8")
        ),
        filename="sample.txt",
    )
    job = isolated.create_job(upload, "MiniMax-M2.7")
    snapshot = wait_for_manager_status(isolated, job.id, {"ready", "failed"})
    assert snapshot["status"] == "ready"

    selected = isolated.condense(job.id, "ten")
    assert len(selected) == 3
    snapshot = wait_for_manager_status(isolated, job.id, {"ready", "completed", "failed"})
    assert snapshot["status"] == "ready"
    assert snapshot["completed_count"] == 2
    assert snapshot["failed_count"] == 1

    retry_ids = isolated.condense(job.id, "failed")
    assert retry_ids == ["ch-2"]
    snapshot = wait_for_manager_status(isolated, job.id, {"completed", "failed"})
    assert snapshot["status"] == "completed"
    assert snapshot["failed_count"] == 0

    export_path = isolated.export_epub(job.id, ["ch-1", "ch-2"])
    assert export_path.exists()
    assert export_path.suffix == ".epub"


def test_prompt_receives_minimum_count_and_stop_batch(tmp_path: Path) -> None:
    client_impl = SlowClient(api_key="server-key")
    isolated = JobManager(storage_dir=tmp_path, ai_client=client_impl, max_workers=1)
    upload = UploadFile(
        file=io.BytesIO(
            (
                "第一章 开端\n这是第一章正文，包含一些内容用于计算最低字数。\n\n"
                "第二章 线索\n这是第二章正文，包含一些内容用于计算最低字数。"
            ).encode("utf-8")
        ),
        filename="sample.txt",
    )
    job = isolated.create_job(upload, "MiniMax-M2.7")
    snapshot = wait_for_manager_status(isolated, job.id, {"ready", "failed"})
    assert snapshot["status"] == "ready"

    isolated.condense(job.id, "one")
    snapshot = wait_for_manager_status(isolated, job.id, {"ready", "completed", "failed"})
    first_count = snapshot["chapters"][0]["original_count"]
    assert client_impl.seen_minimum_count == math.ceil(first_count * 0.2)
    assert client_impl.seen_original_count == first_count

    isolated.condense(job.id, "all")
    stopped = isolated.stop_condense(job.id)
    assert stopped is True
    snapshot = isolated.snapshot(job.id)
    assert snapshot["status"] == "ready"
    assert any(chapter["status"] == "pending" for chapter in snapshot["chapters"])


class DummyClient:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key
        self.api_url = "https://api.minimaxi.com/v1/chat/completions"
        self.seen_api_key = ""
        self.seen_original_count = None
        self.seen_minimum_count = None

    def condense_chapter(
        self,
        title: str,
        text: str,
        model: str,
        api_key: str | None = None,
        api_url: str | None = None,
        original_count: int | None = None,
        minimum_count: int | None = None,
    ) -> str:
        self.seen_api_key = api_key or self.api_key
        self.seen_original_count = original_count
        self.seen_minimum_count = minimum_count
        return f"{title}\n\n浓缩内容"

    def validate_api_key(self, api_key: str | None = None, api_url: str | None = None) -> None:
        self.seen_api_key = api_key or self.api_key


class RejectingClient(DummyClient):
    def __init__(self, api_key: str = "") -> None:
        super().__init__(api_key)
        self.condense_calls = 0

    def validate_api_key(self, api_key: str | None = None, api_url: str | None = None) -> None:
        raise MiniMaxAuthError("MiniMax API Key 验证失败：鉴权未通过。")

    def condense_chapter(
        self,
        title: str,
        text: str,
        model: str,
        api_key: str | None = None,
        api_url: str | None = None,
        original_count: int | None = None,
        minimum_count: int | None = None,
    ) -> str:
        self.condense_calls += 1
        return super().condense_chapter(
            title, text, model, api_key, api_url, original_count, minimum_count
        )


class FlakyClient(DummyClient):
    def __init__(self, api_key: str = "") -> None:
        super().__init__(api_key)
        self.fail_once = {"第二章 线索"}

    def condense_chapter(
        self,
        title: str,
        text: str,
        model: str,
        api_key: str | None = None,
        api_url: str | None = None,
        original_count: int | None = None,
        minimum_count: int | None = None,
    ) -> str:
        if title in self.fail_once:
            self.fail_once.remove(title)
            raise RuntimeError("临时失败")
        return super().condense_chapter(
            title, text, model, api_key, api_url, original_count, minimum_count
        )


class SlowClient(DummyClient):
    def condense_chapter(
        self,
        title: str,
        text: str,
        model: str,
        api_key: str | None = None,
        api_url: str | None = None,
        original_count: int | None = None,
        minimum_count: int | None = None,
    ) -> str:
        time.sleep(0.3)
        return super().condense_chapter(
            title, text, model, api_key, api_url, original_count, minimum_count
        )


def wait_for_status(client: TestClient, job_id: str, statuses: set[str]) -> dict:
    snapshot = None
    for _ in range(80):
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot["status"] in statuses:
            return snapshot
        time.sleep(0.1)
    raise AssertionError(f"job did not reach {statuses}: {snapshot}")


def wait_for_manager_status(manager: JobManager, job_id: str, statuses: set[str]) -> dict:
    snapshot = None
    for _ in range(80):
        snapshot = manager.snapshot(job_id)
        if snapshot and snapshot["status"] in statuses:
            return snapshot
        time.sleep(0.1)
    raise AssertionError(f"job did not reach {statuses}: {snapshot}")
