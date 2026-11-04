"""问数 LLM 客户端：支持 OpenAI 兼容服务与 Ollama，测试用 FakeLLM。

- 复用 OpenAI 官方 Python SDK（openai，Apache-2.0，https://github.com/openai/openai-python）
  的 AsyncOpenAI 调 DashScope compatible-mode，不再手写 httpx 请求 / 重试 / 响应解析。

- 路由用 `Depends(get_llm)` 取客户端，测试 `app.dependency_overrides[get_llm] = lambda: FakeLLM(...)`。
- key 从环境变量或本地 .env 的 DASHSCOPE_API_KEY 读取，环境优先且空串可显式禁用。
  模型 QA_LLM_MODEL、QA_LLM_BASE_URL、QA_LLM_TIMEOUT_S 同样支持 .env，不打印密钥。
- 未配置或停用 → 503 QA_LLM_UNAVAILABLE；Ollama 无鉴权模式不要求 key。
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Annotated, Protocol

from fastapi import Depends
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI, omit

from app.core.errors import AppError
from app.db.session import SessionDep
from app.modules.admin.model_settings import ModelRuntime, environment_model, load_model_runtime

_timing_log = logging.getLogger("app.qa.timing")


@dataclass
class _QaTiming:
    ontology_id: int
    started: float
    stage_started: float
    stage: str = "starting"
    model_count: int = 0
    model_ms: float = 0
    prepare_ms: float | None = None


_timing: ContextVar[_QaTiming | None] = ContextVar("qa_timing", default=None)


def timing_stage(name: str) -> None:
    """Only fixed stage names and durations; never log question/prompt/SQL content."""
    if (timing := _timing.get()) is not None:
        now = time.monotonic()
        _timing_log.info(
            "qa_stage ontologyId=%s stage=%s elapsedMs=%.1f",
            timing.ontology_id,
            timing.stage,
            (now - timing.stage_started) * 1000,
        )
        timing.stage, timing.stage_started = name, now


def observe_answer(func):
    """Request-local timing, including early returns, failures and cancellation."""

    @wraps(func)
    async def wrapped(session, *args, **kwargs):
        oid = session.info.get("ontology_id")
        started = time.monotonic()
        timing = _QaTiming(oid if type(oid) is int else 0, started, started)
        token = _timing.set(timing)
        outcome = "ok"
        try:
            return await func(session, *args, **kwargs)
        except BaseException as error:
            outcome = type(error).__name__
            raise
        finally:
            try:
                timing_stage("finished")
                total_ms = (time.monotonic() - timing.started) * 1000
                _timing_log.info(
                    "qa_total ontologyId=%s outcome=%s totalMs=%.1f modelCount=%s "
                    "modelMs=%.1f prepareMs=%.1f",
                    timing.ontology_id,
                    outcome,
                    total_ms,
                    timing.model_count,
                    timing.model_ms,
                    timing.prepare_ms if timing.prepare_ms is not None else total_ms,
                )
            finally:
                _timing.reset(token)

    return wrapped


async def timed_generate_sql(llm, messages: list[dict[str, str]]) -> str:
    """One logical model call; duration includes the client's existing transport retries."""
    timing = _timing.get()
    if timing is None:
        return await llm.generate_sql(messages)
    started = time.monotonic()
    if timing.prepare_ms is None:
        timing.prepare_ms = (started - timing.started) * 1000
    timing.model_count += 1
    response_chars, outcome = 0, "ok"
    try:
        reply = await llm.generate_sql(messages)
        response_chars = len(reply)
        return reply
    except BaseException as error:
        outcome = type(error).__name__
        raise
    finally:
        duration_ms = (time.monotonic() - started) * 1000
        timing.model_ms += duration_ms
        _timing_log.info(
            "qa_model ontologyId=%s attempt=%s durationMs=%.1f promptChars=%s responseChars=%s outcome=%s",
            timing.ontology_id,
            timing.model_count,
            duration_ms,
            sum(len(message.get("content", "")) for message in messages),
            response_chars,
            outcome,
        )


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
        self._no_auth = not api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._client = AsyncOpenAI(
            api_key=api_key or "unused", base_url=self.base_url, timeout=timeout_s, max_retries=1
        )

    def __repr__(self) -> str:  # 不把 key 带进日志 / traceback
        return f"DashScopeLLM(model={self.model!r})"

    async def generate_sql(self, prompt: list[dict[str, str]]) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=prompt,
                temperature=0,
                **({"extra_headers": {"Authorization": omit}} if self._no_auth else {}),
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
    runtime = runtime or environment_model("qa")
    if not runtime.value.enabled or (
        runtime.value.auth_mode == "key" and not runtime.api_key.get_secret_value()
    ):
        raise QaLlmUnavailable("问数大模型已停用或未配置密钥")
    return DashScopeLLM(
        runtime.api_key.get_secret_value(),
        runtime.value.model,
        runtime.value.base_url,
        runtime.value.timeout_sec,
    )
