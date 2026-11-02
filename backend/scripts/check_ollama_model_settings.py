"""Offline provider/auth/URL/runtime/directory checks; no live DB, HTTP or model inference."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace as Namespace
from unittest.mock import AsyncMock, patch

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from app.core.errors import AppError
from app.modules.admin import model_settings as config
from app.modules.auto_ontology import llm as ontology
from app.modules.qa import llm as qa


async def main():
    old = {"model": "offline-model", "baseUrl": "https://unit.test/v1", "timeoutSec": 30.0, "enabled": True}
    assert config.ModelValue.model_validate(old).provider == "openai"
    local = config.ModelValue.model_validate(
        {**old, "provider": "ollama", "baseUrl": "http://localhost:11434/"}
    )
    assert local.base_url == "http://localhost:11434/v1" and local.auth_mode == "none"
    assert (
        config.ModelValue.model_validate(
            {**old, "provider": "ollama", "baseUrl": local.base_url + "/"}
        ).base_url
        == local.base_url
    )
    for url in (
        "ftp://host",
        "http://user:secret@host",
        "https://host?key=x",
        "https://host/#fragment",
        "http://host:0",
        "https://host/a\nb",
    ):
        try:
            config.ModelValue.model_validate({**old, "baseUrl": url})
        except ValidationError:
            pass
        else:
            raise AssertionError("invalid URL accepted")
    draft = config.ModelConnectionIn.model_validate(
        {"provider": "ollama", "timeoutSec": 30.0, "enabled": True}
    )
    assert draft.model is None and draft.auth_mode == "none" and draft.base_url == local.base_url
    try:
        config.ModelSaveIn.model_validate({**draft.model_dump(), "lockVersion": 0, "model": ""})
    except ValidationError:
        pass
    else:
        raise AssertionError("blank saved model")
    settings = Namespace(
        llm_api_key=SecretStr("offline-unit-key"),
        qa_llm_provider="openai",
        auto_llm_provider="openai",
        qa_llm_model=None,
        auto_llm_model=None,
        qa_llm_base_url=old["baseUrl"],
        auto_llm_base_url=None,
        qa_llm_timeout_s=None,
        auto_llm_timeout_s=None,
    )
    row = Namespace(value=old, secret_enc=None, lock_version=2, updated_at=datetime(2026, 11, 2, tzinfo=UTC))
    session = Namespace(scalar=AsyncMock(return_value=row))
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(config, "get_settings", return_value=settings),
    ):
        runtime = await config.load_model_runtime(session, "qa")
        assert runtime.api_key.get_secret_value() == "offline-unit-key" and runtime.value.provider == "openai"
        row.value = {**old, "baseUrl": "https://different.test/v1"}
        assert not (await config.load_model_runtime(session, "qa")).api_key.get_secret_value()
        with patch.dict(
            "os.environ",
            {
                "QA_LLM_PROVIDER": "ollama",
                "QA_LLM_BASE_URL": "http://localhost:11434",
                "DASHSCOPE_API_KEY": "must-not-send",
            },
        ):
            runtime = config.environment_model("qa")
            assert (
                runtime.value.enabled
                and runtime.value.auth_mode == "none"
                and not runtime.api_key.get_secret_value()
            )
        saved = config.ModelRuntime(
            config.ModelValue.model_validate(old), SecretStr("offline-saved-key"), "database", "database"
        )
        with patch.object(config, "load_model_runtime", AsyncMock(return_value=saved)) as load:
            assert await config.connection_key(session, "qa", draft) == ""
            load.assert_not_awaited()
            assert (
                await config.connection_key(session, "qa", config.ModelConnectionIn.model_validate(old))
                == "offline-saved-key"
            )
            try:
                await config.connection_key(
                    session,
                    "qa",
                    config.ModelConnectionIn.model_validate({**old, "provider": "ollama", "authMode": "key"}),
                )
            except AppError as exc:
                assert exc.code == "MODEL_SECRET_REQUIRED"
            else:
                raise AssertionError("cross-provider credential reuse")

    requests, response_mode = [], ["ok"]

    def directory(request):
        requests.append(request)
        assert (
            request.method == "GET"
            and request.url.path == "/v1/models"
            and "authorization" not in request.headers
        )
        if response_mode[0] == "denied":
            return httpx.Response(401, text="private-upstream-body")
        return httpx.Response(200, json={"data": [{"id": "qwen3:8b"}]})

    original_client = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["verify"] is True and kwargs["follow_redirects"] is False
        return original_client(transport=httpx.MockTransport(directory), **kwargs)

    with patch.object(config.httpx, "AsyncClient", client):
        result = await config.model_connection("qa", draft, session, Namespace())
        assert (
            result["ok"]
            and result["selectedModelAvailable"] is None
            and result["modelOptions"] == [{"value": "qwen3:8b", "label": "qwen3:8b"}]
        )
        response_mode[0] = "denied"
        result = await config.model_connection("qa", draft, session, Namespace())
        assert not result["ok"] and "401" in result["error"] and "private-upstream-body" not in str(result)
    assert len(requests) == 2

    def completion(request):
        assert request.url.path == "/v1/chat/completions" and "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": "qwen3:8b",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}
                ],
            },
        )

    runtime = config.ModelRuntime(local, SecretStr(""), "database", "none")
    wrapper = qa.get_llm(runtime)
    await wrapper._client.close()
    wrapper._client = AsyncOpenAI(
        api_key="unused",
        base_url=local.base_url,
        http_client=original_client(transport=httpx.MockTransport(completion)),
    )
    assert await wrapper.generate_sql([{"role": "user", "content": "offline synthetic"}]) == "{}"
    await wrapper._client.close()
    clients = []

    def sdk(**kwargs):
        client = AsyncOpenAI(**kwargs, http_client=original_client(transport=httpx.MockTransport(completion)))
        clients.append(client)
        return client

    with patch.object(ontology, "AsyncOpenAI", sdk):
        assert (
            await ontology.get_llm(runtime).complete([{"role": "user", "content": "offline synthetic"}])
            == "{}"
        )
        assert clients[-1].is_closed()
    for bad in (
        Namespace(choices=None),
        Namespace(choices=[]),
        Namespace(choices=[None]),
        Namespace(choices=[Namespace(message=None)]),
        Namespace(choices=[Namespace(message=Namespace(content=42))]),
    ):

        class BadClient:
            def __init__(self, reply):
                self.reply = reply

            async def __aenter__(self):
                return Namespace(
                    chat=Namespace(completions=Namespace(create=AsyncMock(return_value=self.reply)))
                )

            async def __aexit__(self, *args):
                self.closed = True

        client = BadClient(bad)
        with patch.object(ontology, "AsyncOpenAI", return_value=client):
            try:
                await ontology.get_llm(runtime).complete([])
            except ontology.LlmError:
                assert client.closed
            else:
                raise AssertionError("invalid completion accepted")
    print("model settings: provider/URL/auth/key scope/directory/no-key/invalid replies passed (offline)")


if __name__ == "__main__":
    asyncio.run(main())
