from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app import config
from app.text_utils import normalize_text, strip_reasoning


class MiniMaxError(RuntimeError):
    pass


class MiniMaxAuthError(MiniMaxError):
    pass


@dataclass
class MiniMaxClient:
    api_key: str = config.MINIMAX_API_KEY
    api_url: str = config.MINIMAX_API_URL
    mock_mode: bool = config.MOCK_AI
    timeout_seconds: float = 300.0
    retries: int = 3

    def validate_api_key(self, api_key: str | None = None, api_url: str | None = None) -> None:
        if self.mock_mode:
            return
        effective_api_key = (api_key or self.api_key or "").strip()
        if not effective_api_key:
            raise MiniMaxAuthError("缺少 MiniMax API Key。")
        request_url = api_url or self.api_url

        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.get(
                    self._models_url(request_url),
                    headers={"Authorization": f"Bearer {effective_api_key}"},
                )
        except Exception as exc:
            raise MiniMaxError(f"MiniMax Key 预检失败：{exc}") from exc

        if response.status_code in {401, 403}:
            raise MiniMaxAuthError(
                "MiniMax API Key 验证失败：鉴权未通过。请确认填写的是 MiniMax 平台的有效 API Key。"
            )
        if response.status_code >= 400:
            raise MiniMaxError(
                f"MiniMax Key 预检失败：HTTP {response.status_code} {response.text[:300]}"
            )

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
        if self.mock_mode:
            return self._mock_condense(title, text)
        effective_api_key = (api_key or self.api_key or "").strip()
        if not effective_api_key:
            raise MiniMaxError(
                "服务端缺少 MINIMAX_API_KEY。请在启动服务或 Docker 容器时设置该环境变量。"
            )
        request_url = api_url or self.api_url

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是严谨的书籍浓缩器。请在保留章节情节、事实、人物关系、论证链条和关键信息的前提下，"
                        "尽可能减少字数。可以压缩景物描写，合并重复表达，总结对话内容。"
                        "浓缩后字数不得少于用户指定的最低字数。"
                        "如果正文中出现 [[BOOK_CONDENSER_IMAGE:...]] 图片占位符，必须逐字原样保留，"
                        "并尽量保持在对应内容附近。"
                        "只输出浓缩后的正文，不要输出解释、标题外的说明或 Markdown 包装。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"章节标题：{title}\n\n"
                        f"本章原文字数约 {original_count or '未知'} 字，"
                        f"浓缩后不得少于 {minimum_count or '原文 20%'} 字。\n\n"
                        "图片占位符形如 [[BOOK_CONDENSER_IMAGE:img-1]]，请原样保留。\n\n"
                        "请浓缩以下章节，保留原章节结构中的核心信息：\n\n"
                        f"{text}"
                    ),
                },
            ],
            "temperature": 0.3,
            "top_p": 0.95,
            "max_completion_tokens": self._max_completion_tokens(text),
            "reasoning_split": True,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        request_url,
                        headers={
                            "Authorization": f"Bearer {effective_api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                if response.status_code in {401, 403}:
                    raise MiniMaxAuthError(
                        "MiniMax API Key 鉴权失败。请确认 key 有效，并且可访问 OpenAI 兼容接口。"
                    )
                response.raise_for_status()
                data = response.json()
                return self._extract_content(data)
            except MiniMaxAuthError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 8))
        raise MiniMaxError(f"MiniMax 调用失败：{last_error}") from last_error

    def _models_url(self, api_url: str) -> str:
        if "/chat/completions" in api_url:
            return api_url.split("/chat/completions", 1)[0] + "/models"
        if "/text/chatcompletion_v2" in api_url:
            return api_url.split("/text/chatcompletion_v2", 1)[0] + "/models"
        if api_url.rstrip("/").endswith("/v1"):
            return api_url.rstrip("/") + "/models"
        return "https://api.minimax.io/v1/models"

    def _extract_content(self, data: dict) -> str:
        try:
            content = data["choices"][0]["message"].get("content", "")
        except (KeyError, IndexError, TypeError) as exc:
            raise MiniMaxError(f"MiniMax 响应格式异常：{data}") from exc
        content = strip_reasoning(content)
        if not content:
            raise MiniMaxError("MiniMax 返回了空内容。")
        return content

    def _max_completion_tokens(self, text: str) -> int:
        return max(1024, min(24000, len(text) // 2))

    def _mock_condense(self, title: str, text: str) -> str:
        normalized = normalize_text(text)
        sentences = [
            part.strip()
            for part in normalized.replace("。", "。\n")
            .replace("！", "！\n")
            .replace("？", "？\n")
            .replace(". ", ".\n")
            .splitlines()
            if part.strip()
        ]
        if not sentences:
            return f"{title}\n\n{normalized[: max(120, len(normalized) // 10)]}"
        keep = max(2, min(len(sentences), len(sentences) // 5 or 1))
        selected = sentences[: max(1, keep // 2)] + sentences[-max(1, keep - keep // 2) :]
        return normalize_text(f"{title}\n\n" + "\n".join(selected))
