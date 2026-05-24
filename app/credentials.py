from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from app import config


@dataclass
class MiniMaxCredential:
    api_key: str
    region: str = config.DEFAULT_REGION
    api_url: str = ""

    @property
    def resolved_api_url(self) -> str:
        return self.api_url or config.REGION_ENDPOINTS.get(self.region, config.MINIMAX_API_URL)


class CredentialStore:
    def __init__(self, storage_dir: Path = config.STORAGE_DIR) -> None:
        self.path = storage_dir / "config" / "minimax_credentials.json"

    def load(self) -> MiniMaxCredential | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        api_key = str(data.get("api_key", "")).strip()
        if not api_key:
            return None
        region = str(data.get("region", config.DEFAULT_REGION)).strip().lower()
        if region not in config.REGION_ENDPOINTS:
            region = config.DEFAULT_REGION
        api_url = str(data.get("api_url", "")).strip()
        return MiniMaxCredential(api_key=api_key, region=region, api_url=api_url)

    def save(self, api_key: str, region: str, api_url: str = "") -> None:
        api_key = api_key.strip()
        if not api_key:
            return
        region = region.strip().lower()
        if region not in config.REGION_ENDPOINTS:
            region = config.DEFAULT_REGION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "api_key": api_key,
            "region": region,
            "api_url": api_url.strip(),
        }
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.path)
        os.chmod(self.path, 0o600)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
