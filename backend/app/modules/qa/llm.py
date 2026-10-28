"""问数 LLM 客户端（可插拔）：真实实现走 DashScope OpenAI 兼容接口，测试用 FakeLLM。

- 复用 OpenAI 官方 Python SDK（openai，Apache-2.0，https://github.com/openai/openai-python）
  的 AsyncOpenAI 调 DashScope compatible-mode，不再手写 httpx 请求 / 重试 / 响应解析。

- 路由用 `Depends(get_llm)` 取客户端，测试 `app.dependency_overrides[get_llm] = lambda: FakeLLM(...)`。
- key 从环境变量或本地 .env 的 DASHSCOPE_API_KEY 读取，环境优先且空串可显式禁用。
  模型 QA_LLM_MODEL、QA_LLM_BASE_URL、QA_LLM_TIMEOUT_S 同样支持 .env，不打印密钥。
- 没配 key → 503 QA_LLM_UNAVAILABLE（不在启动时报错，其它接口照常可用）。
"""

from __future__ import annotations

import os
from typing import Annotated, Protocol

from fastapi import Depends
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import SessionDep
from app.modules.admin.model_settings import ModelRuntime, load_model_runtime


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
    """AsyncOpenAI(base_url=DashScope).chat.completions.create，返回首条回复原文（SQL 由调用方抽取）。"""

    def __init__(self, api_key: str, model: str, base_url: str, timeout_s: float) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._client = AsyncOpenAI(api_key=api_key, base_url=self.base_url, timeout=timeout_s, max_retries=1)

    def __repr__(self) -> str:  # 不把 key 带进日志 / traceback
        return f"DashScopeLLM(model={self.model!r})"

    async def generate_sql(self, prompt: list[dict[str, str]]) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=prompt,
                temperature=0,
            )
        except APIStatusError as error:
            raise QaLlmFailed(f"大模型返回 {error.status_code}") from None
        except APIConnectionError as error:
            raise QaLlmFailed(f"调用大模型失败：{type(error).__name__}") from None
        except APIError:
            raise QaLlmFailed("大模型返回格式无法解析") from None
        if not response.choices:
            raise QaLlmFailed("大模型返回格式无法解析")
        return response.choices[0].message.content or ""


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


async def configured_runtime(session: SessionDep) -> ModelRuntime:
    return await load_model_runtime(session, "qa")


def get_llm(runtime: Annotated[ModelRuntime | None, Depends(configured_runtime)] = None) -> LLMClient:
    if runtime is not None:
        if not runtime.value.enabled or not runtime.api_key.get_secret_value():
            raise QaLlmUnavailable("问数大模型已停用或未配置密钥")
        return DashScopeLLM(
            runtime.api_key.get_secret_value(),
            runtime.value.model,
            runtime.value.base_url,
            runtime.value.timeout_sec,
        )
    settings = get_settings()
    file_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    key = os.environ.get("DASHSCOPE_API_KEY", file_key).strip()
    if not key:
        raise QaLlmUnavailable()
    return DashScopeLLM(
        key,
        os.environ.get("QA_LLM_MODEL") or settings.qa_llm_model or DEFAULT_MODEL,
        os.environ.get("QA_LLM_BASE_URL") or settings.qa_llm_base_url or DEFAULT_BASE_URL,
        float(os.environ.get("QA_LLM_TIMEOUT_S") or settings.qa_llm_timeout_s or 30),
    )
