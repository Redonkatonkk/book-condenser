from __future__ import annotations

import os
from pathlib import Path


SUPPORTED_MODELS = [
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed",
    "MiniMax-M2.5",
    "MiniMax-M2.5-highspeed",
    "MiniMax-M2.1",
    "MiniMax-M2.1-highspeed",
    "MiniMax-M2",
]

REGION_ENDPOINTS = {
    "cn": "https://api.minimaxi.com/v1/chat/completions",
    "global": "https://api.minimax.io/v1/chat/completions",
}

REGION_LABELS = {
    "cn": "国内版 minimax.cn / minimaxi.com",
    "global": "国际版 minimax.io",
}

DEFAULT_REGION = os.getenv("MINIMAX_REGION", "cn").lower()
if DEFAULT_REGION not in REGION_ENDPOINTS:
    DEFAULT_REGION = "cn"

DEFAULT_MODEL = os.getenv("MINIMAX_DEFAULT_MODEL", "MiniMax-M2.7")
if DEFAULT_MODEL not in SUPPORTED_MODELS:
    DEFAULT_MODEL = "MiniMax-M2.7"

MINIMAX_API_URL = os.getenv("MINIMAX_API_URL", REGION_ENDPOINTS[DEFAULT_REGION])
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")

MAX_WORKERS = max(1, int(os.getenv("BOOK_CONDENSER_WORKERS", "4")))
STORAGE_DIR = Path(os.getenv("BOOK_CONDENSER_STORAGE", "storage")).resolve()
MOCK_AI = os.getenv("BOOK_CONDENSER_MOCK_AI", "0").lower() in {"1", "true", "yes"}

MAX_UPLOAD_BYTES = int(os.getenv("BOOK_CONDENSER_MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {".epub", ".pdf", ".txt"}
