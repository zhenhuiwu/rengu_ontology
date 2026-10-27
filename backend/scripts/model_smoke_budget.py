"""One isolated, persistent 10-question / 9-CNY-reservation smoke budget; never used by main."""

import asyncio
import hashlib
import json
import os
from contextvars import ContextVar
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi import Request
from openai import APIError, AsyncOpenAI

from app.core.errors import AppError
from app.modules.qa.llm import QaLlmFailed

MODEL = "qwen-flash"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
PROMPT_BYTES = 96 * 1024
OUTPUT_TOKENS = 4096
INPUT_BOUND = 2 * PROMPT_BYTES + 4096
INPUT_PRICE = Decimal("1.835")
OUTPUT_PRICE = Decimal("14.678")
CALL_BOUND = (INPUT_BOUND * INPUT_PRICE + OUTPUT_TOKENS * OUTPUT_PRICE) / 1_000_000
RESERVATION = Decimal("0.45")
TOTAL_RESERVATION = Decimal("9")
ORIGINAL_SEND = httpx.AsyncHTTPTransport.handle_async_request
BACKEND = Path(__file__).resolve().parents[1]


async def configured_runtime():
    """Read only the existing deployment; never save a secret, settings row or environment file."""
    import asyncpg
    from cryptography.fernet import Fernet, InvalidToken, MultiFernet
    from dotenv import dotenv_values
    from pydantic import SecretStr
    from sqlalchemy.engine import make_url

    from app.modules.admin.model_settings import ModelRuntime, ModelValue
    from app.modules.data.secrets import _DEV_SEED, PREFIX

    primary = dotenv_values(BACKEND / ".env")
    alternate = dotenv_values(Path("D:/项目/palantir-ontology/auto-ontology/.env"))
    source = make_url(primary.get("RENGU_DATABASE_URL") or "")
    if (
        source.host not in ("localhost", "127.0.0.1")
        or source.port != 5433
        or source.database != "rengu_ontology"
    ):
        raise blocked("模型配置来源不是已批准的本地主库，只读准备已拒绝")
    connection = await asyncpg.connect(
        source.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        async with connection.transaction(readonly=True):
            row = await connection.fetchrow(
                "SELECT value,secret_enc FROM platform_setting WHERE code='model:qa'"
            )
    finally:
        await connection.close()
    value = (
        ModelValue.model_validate(json.loads(row["value"]))
        if row
        else ModelValue(
            model=primary.get("QA_LLM_MODEL") or MODEL,
            base_url=primary.get("QA_LLM_BASE_URL") or BASE_URL,
            timeout_sec=float(primary.get("QA_LLM_TIMEOUT_S") or 30),
            enabled=True,
        )
    )
    key = primary.get("DASHSCOPE_API_KEY") or ""
    key_source = "environment"
    if row and row["secret_enc"]:
        import base64

        # The test datasource uses a different process key: decrypt main settings only with the
        # original ignored deployment key, without temporarily mutating process os.environ.
        raw = (primary.get("RENGU_DS_SECRET_KEY") or "").strip()
        if not raw and primary.get("RENGU_ENV") == "prod":
            raise blocked("主配置缺少解密材料，未用测试数据源密钥替代")
        keys = [part.strip().encode() for part in raw.split(",") if part.strip()] or [
            base64.urlsafe_b64encode(hashlib.sha256(_DEV_SEED).digest())
        ]
        try:
            stored = row["secret_enc"]
            if not stored.startswith(PREFIX):
                raise ValueError
            key = (
                MultiFernet([Fernet(part) for part in keys]).decrypt(stored[len(PREFIX) :].encode()).decode()
            )
        except (InvalidToken, ValueError, TypeError):
            raise blocked("主配置模型密钥无法解密，未回退其他凭据") from None
        key_source = "database"
    elif not key:
        if alternate.get("DEFAULT_MODELS_ENDPOINT", "").rstrip("/") != BASE_URL or alternate.get(
            "DEFAULT_MODELS_MODEL"
        ) not in (MODEL, f"openai/{MODEL}"):
            raise blocked("备用部署的模型或地址与已核价配置不一致")
        key = alternate.get("DEFAULT_MODELS_API_KEY") or ""
    if value.model != MODEL or value.base_url.rstrip("/") != BASE_URL or not key:
        raise blocked("已配置模型、地址或凭据未满足本轮验收边界")
    return ModelRuntime(value, SecretStr(key), "database" if row else "environment", key_source)


def blocked(message="本轮模型验收边界已达到，请停止并查看验收记录"):
    return AppError(message, code="MODEL_SMOKE_LIMIT", status_code=409)


def deny_other_http():
    """Every other SDK, embedding, judge, tool and business HTTP outlet is closed in this process."""

    def denied(*args, **kwargs):
        raise blocked("隔离验收进程禁止其他外部调用")

    async def denied_async(*args, **kwargs):
        denied()

    httpx.HTTPTransport.handle_request = denied
    httpx.AsyncHTTPTransport.handle_async_request = denied_async


class SmokeBudget:
    def __init__(self, runtime, questions: list[str], ledger: Path, *, sender=None):
        if runtime.value.model != MODEL or runtime.value.base_url.rstrip("/") != BASE_URL:
            raise blocked("实际模型或地址与已核价配置不一致，未发起调用")
        if not runtime.value.enabled or not runtime.api_key.get_secret_value():
            raise blocked("已配置模型不可用，未发起调用")
        if len(questions) != 10 or len(set(questions)) != 10 or any(not q.strip() for q in questions):
            raise blocked("本轮必须固定10个不同的验收问题")
        self.questions = {q.strip(): index + 1 for index, q in enumerate(questions)}
        self.ledger = Path(ledger)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.lease = self.ledger.with_suffix(".lock")
        # A crash leaves the lease in place and fails closed. An operator must verify the old PID
        # is gone before removing only this lease; the budget JSON must never be reset.
        self.lease_fd = os.open(self.lease, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(self.lease_fd, str(os.getpid()).encode("ascii"))
        config = {
            "model": MODEL,
            "baseUrl": BASE_URL,
            "questionsHash": hashlib.sha256(json.dumps(questions, ensure_ascii=False).encode()).hexdigest(),
            "credentialHash": hashlib.sha256(runtime.api_key.get_secret_value().encode()).hexdigest(),
            "reservation": str(RESERVATION),
            "limit": str(TOTAL_RESERVATION),
        }
        try:
            self.state = (
                json.loads(self.ledger.read_text("utf-8"))
                if self.ledger.exists()
                else {
                    "config": config,
                    "requests": [],
                    "questionRequests": [],
                    "halted": False,
                }
            )
            if self.state["config"] != config or len(self.state["requests"]) > 20:
                raise blocked("已有验收账本配置不一致，未重置预算")
            assert CALL_BOUND < RESERVATION and 20 * RESERVATION == TOTAL_RESERVATION < 10
            self._persist()
        except BaseException:
            os.close(self.lease_fd)
            self.lease.unlink()
            raise
        self.serial = asyncio.Lock()
        self.sends = asyncio.Lock()
        self.question_id = ContextVar("model_smoke_question", default=None)
        self._network = httpx.AsyncHTTPTransport(retries=0)
        self.sender = sender
        self.client = AsyncOpenAI(
            api_key=runtime.api_key.get_secret_value(),
            base_url=BASE_URL,
            timeout=min(runtime.value.timeout_sec, 60),
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(self._send), follow_redirects=False, trust_env=False
            ),
        )
        deny_other_http()

    def _persist(self):
        temporary = self.ledger.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(self.state, output, ensure_ascii=True, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.ledger)

    def summary(self):
        return {
            "model": MODEL,
            "requests": len(self.state["requests"]),
            "reservedCny": str(len(self.state["requests"]) * RESERVATION),
            "limitCny": str(TOTAL_RESERVATION),
            "hardUserLimitCny": "10",
            "halted": self.state["halted"],
            "records": self.state["requests"],
        }

    async def dependency(self, request: Request):
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("question"), str):
            raise blocked("问题格式不在本轮验收范围内")
        question = body["question"].strip()
        identity = self.questions.get(question)
        if (
            identity is None
            or request.method != "POST"
            or not request.url.path.endswith(("/turns", "/turns:stream"))
        ):
            raise blocked("问题不在本轮固定10题范围内")
        async with self.serial:
            # SSE fallback reuses the same question's two provider calls, never a fresh retry budget.
            self.state["questionRequests"].append(identity)
            self._persist()
            token = self.question_id.set(identity)
            try:
                yield self
            finally:
                self.question_id.reset(token)

    async def generate_sql(self, prompt):
        try:
            response = await self.client.chat.completions.create(
                model=MODEL,
                messages=prompt,
                temperature=0,
                n=1,
                max_tokens=OUTPUT_TOKENS,
                extra_body={"enable_thinking": False, "enable_search": False},
            )
        except AppError:
            raise
        except APIError as error:
            if isinstance(error.__cause__, AppError):
                raise error.__cause__ from None
            raise QaLlmFailed("本轮受控模型调用失败；已保留费用预留，不追加隐藏重试") from None
        if not response.choices or response.choices[0].finish_reason == "length":
            raise QaLlmFailed("模型回答为空或达到本轮输出上限")
        return response.choices[0].message.content or ""

    async def _send(self, request: httpx.Request):
        async with self.sends:
            return await self._send_locked(request)

    async def _send_locked(self, request: httpx.Request):
        identity = self.question_id.get()
        data = json.loads(request.content)
        messages = data.get("messages")
        if (
            request.method != "POST"
            or str(request.url) != f"{BASE_URL}/chat/completions"
            or set(data)
            != {"model", "messages", "temperature", "n", "max_tokens", "enable_thinking", "enable_search"}
            or data.get("model") != MODEL
            or data.get("n") != 1
            or data.get("max_tokens") != OUTPUT_TOKENS
            or data.get("enable_thinking") is not False
            or data.get("enable_search") is not False
            or identity not in self.questions.values()
            or not isinstance(messages, list)
            or not 1 <= len(messages) <= 32
            or any(
                not isinstance(m, dict)
                or set(m) != {"role", "content"}
                or m["role"] not in ("system", "user", "assistant")
                or not isinstance(m["content"], str)
                for m in messages
            )
            or len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) > PROMPT_BYTES
        ):
            raise blocked("模型请求超过已证明的输入、输出或功能边界")
        records = self.state["requests"]
        if self.state["halted"] or len(records) >= 20 or sum(r["question"] == identity for r in records) >= 2:
            raise blocked()
        record = {
            "question": identity,
            "attempt": 1 + sum(r["question"] == identity for r in records),
            "reservedCny": str(RESERVATION),
            "state": "reserved",
            "usage": None,
        }
        records.append(record)
        self._persist()  # Durable debit BEFORE the network, even on timeout, cancellation or process crash.
        try:
            response = await (self.sender(request) if self.sender else ORIGINAL_SEND(self._network, request))
            await response.aread()
            record["httpStatus"] = response.status_code
            record["state"] = "response" if response.is_success else "http_error"
            if response.is_success:
                payload = response.json()
                usage = payload.get("usage") or {}
                input_count, output_count = usage.get("prompt_tokens"), usage.get("completion_tokens")
                reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
                if (
                    type(input_count) is not int
                    or type(output_count) is not int
                    or not 0 <= input_count <= INPUT_BOUND
                    or not 0 <= output_count <= OUTPUT_TOKENS
                    or reasoning not in (0, None)
                ):
                    self.state["halted"] = True
                    raise blocked("模型用量未满足已证明的预算边界，已停止后续调用")
                record["usage"] = {
                    "promptTokens": input_count,
                    "completionTokens": output_count,
                    "reasoningTokens": reasoning or 0,
                }
                record["listPriceUpperCny"] = str(
                    (input_count * INPUT_PRICE + output_count * OUTPUT_PRICE) / 1_000_000
                )
            else:
                self.state["halted"] = True  # No trustworthy usage accompanies an HTTP failure.
            return response
        except BaseException as error:
            record["state"] = "failed"
            if record["usage"] is None:
                self.state["halted"] = True
            record["errorType"] = type(
                error
            ).__name__  # Never record exception strings or request/response bodies.
            raise
        finally:
            self._persist()

    async def close(self):
        await self.client.close()
        await self._network.aclose()
        os.close(self.lease_fd)
        self.lease.unlink()
