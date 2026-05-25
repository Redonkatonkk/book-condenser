from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app import config
from app.credentials import MiniMaxCredential


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_ITERATIONS = 260_000
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30


@dataclass(frozen=True)
class User:
    id: str
    email: str
    created_at: float
    api_key: str = ""
    region: str = config.DEFAULT_REGION
    api_url: str = ""

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key.strip())


class UserStore:
    def __init__(self, storage_dir: Path = config.STORAGE_DIR) -> None:
        self.storage_dir = storage_dir
        self.path = storage_dir / "app.db"
        self._ensure_schema()

    def configure(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.path = storage_dir / "app.db"
        self._ensure_schema()

    def create_user(self, email: str, password: str) -> User:
        normalized_email = self._normalize_email(email)
        self._validate_password(password)
        now = time.time()
        user_id = uuid.uuid4().hex
        password_hash = self._hash_password(password)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users (id, email, password_hash, created_at, region)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, normalized_email, password_hash, now, config.DEFAULT_REGION),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("这个邮箱已经注册。") from exc
        return User(id=user_id, email=normalized_email, created_at=now)

    def authenticate(self, email: str, password: str) -> User | None:
        normalized_email = self._normalize_email(email)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
        if not row or not self._verify_password(password, row["password_hash"]):
            return None
        return self._row_to_user(row)

    def get_user(self, user_id: str) -> User | None:
        if not user_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (token_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (self._token_hash(token), user_id, now, now + SESSION_TTL_SECONDS),
            )
        return token

    def get_user_by_session(self, token: str) -> User | None:
        if not token:
            return None
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            row = conn.execute(
                """
                SELECT users.*
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (self._token_hash(token), now),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def delete_session(self, token: str) -> None:
        if not token:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),))

    def get_credential(self, user_id: str) -> MiniMaxCredential | None:
        user = self.get_user(user_id)
        if not user or not user.api_key.strip():
            return None
        region = user.region if user.region in config.REGION_ENDPOINTS else config.DEFAULT_REGION
        return MiniMaxCredential(
            api_key=user.api_key,
            region=region,
            api_url=user.api_url,
        )

    def save_api_key(self, user_id: str, api_key: str, region: str, api_url: str = "") -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("API Key 不能为空。")
        normalized_region = region.strip().lower()
        if normalized_region not in config.REGION_ENDPOINTS:
            normalized_region = config.DEFAULT_REGION
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE users
                SET api_key = ?, region = ?, api_url = ?
                WHERE id = ?
                """,
                (api_key, normalized_region, api_url.strip(), user_id),
            )
        if cursor.rowcount == 0:
            raise ValueError("用户不存在。")

    def clear_api_key(self, user_id: str) -> None:
        if not user_id:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET api_key = '', api_url = '' WHERE id = ?",
                (user_id,),
            )

    def _ensure_schema(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    api_key TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT '',
                    api_url TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _normalize_email(self, email: str) -> str:
        normalized = email.strip().lower()
        if not EMAIL_RE.fullmatch(normalized):
            raise ValueError("请输入有效邮箱。")
        return normalized

    def _validate_password(self, password: str) -> None:
        if len(password) < 6:
            raise ValueError("密码至少需要 6 位。")

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            PASSWORD_ITERATIONS,
        )
        return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        try:
            algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iterations),
            )
            return hmac.compare_digest(digest.hex(), digest_hex)
        except (ValueError, TypeError):
            return False

    def _token_hash(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _row_to_user(self, row: sqlite3.Row) -> User:
        region = str(row["region"] or config.DEFAULT_REGION).strip().lower()
        if region not in config.REGION_ENDPOINTS:
            region = config.DEFAULT_REGION
        return User(
            id=str(row["id"]),
            email=str(row["email"]),
            created_at=float(row["created_at"]),
            api_key=str(row["api_key"] or ""),
            region=region,
            api_url=str(row["api_url"] or ""),
        )
