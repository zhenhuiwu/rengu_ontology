"""问数 LLM 客户端（可插拔）：真实实现走 DashScope OpenAI 兼容接口，测试用 FakeLLM。

- 路由用 `Depends(get_llm)` 取客户端，测试 `app.dependency_overrides[get_llm] = lambda: FakeLLM(...)`。
- key 只读环境变量 DASHSCOPE_API_KEY，不硬编码、不打印；模型 QA_LLM_MODEL（默认 qwen-flash）、
  QA_LLM_BASE_URL、QA_LLM_TIMEOUT_S 也走环境变量。
  [推断] 不进 app/core/config（共享骨架锁定），后续可迁入 Settings。
- 没配 key → 503 QA_LLM_UNAVAILABLE（不在启动时报错，其它接口照常可用）。
"""

from __future__ import annotations

import os
from typing import Protocol

import httpx

from app.core.errors import AppError


class QaLlmUnavailable(AppError):
    status_code = 503
    code = "QA_LLM_UNAVAILABLE"
    message = "问数大模型未配置（缺少 DASHSCOPE_API_KEY）"


class QaLlmFailed(AppError):
    status_code = 502
    code = "QA_LLM_FAILED"
    message = "调用大模型失败"


class LLMClient(Protocol):
    async def generate_sql(self, prompt: list[dict[str, str]]) -> str: ...


class DashScopeLLM:
    """POST {base}/chat/completions，返回 choices[0].message.content 原文（SQL 由调用方抽取）。"""

    def __init__(self, api_key: str, model: str, base_url: str, timeout_s: float) -> None:
        self._key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def __repr__(self) -> str:  # 不把 key 带进日志 / traceback
        return f"DashScopeLLM(model={self.model!r})"

    async def generate_sql(self, prompt: list[dict[str, str]]) -> str:
        body = {"model": self.model, "messages": prompt, "temperature": 0}
        headers = {"Authorization": f"Bearer {self._key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise QaLlmFailed(f"调用大模型失败：{type(e).__name__}") from None
        if r.status_code != 200:
            raise QaLlmFailed(f"大模型返回 {r.status_code}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise QaLlmFailed("大模型返回格式无法解析") from None


class FakeLLM:
    """测试用：固定返回给定文本，记下收到的 prompt。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[list[dict[str, str]]] = []

    async def generate_sql(self, prompt: list[dict[str, str]]) -> str:
        self.prompts.append(prompt)
        return self.reply


DEFAULT_MODEL = "qwen-flash"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def get_llm() -> LLMClient:
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise QaLlmUnavailable()
    return DashScopeLLM(
        key,
        os.environ.get("QA_LLM_MODEL") or DEFAULT_MODEL,
        os.environ.get("QA_LLM_BASE_URL") or DEFAULT_BASE_URL,
        float(os.environ.get("QA_LLM_TIMEOUT_S") or 30),
    )
