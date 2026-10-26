"""模型本地配置与环境覆盖，不连接外部模型。"""

import pytest

from app.core.config import Settings
from app.modules.auto_ontology import llm as auto_llm
from app.modules.qa import llm as qa_llm


@pytest.mark.parametrize("module,prefix", [(qa_llm, "QA"), (auto_llm, "AUTO_ONTOLOGY")])
def test_dotenv_model_and_explicit_environment_override(tmp_path, monkeypatch, module, prefix):
    for name in (
        "DASHSCOPE_API_KEY",
        "QA_LLM_MODEL",
        "QA_LLM_BASE_URL",
        "QA_LLM_TIMEOUT_S",
        "AUTO_ONTOLOGY_LLM_MODEL",
        "AUTO_ONTOLOGY_LLM_BASE_URL",
        "AUTO_ONTOLOGY_LLM_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        f"DASHSCOPE_API_KEY=local-secret-for-test\n{prefix}_LLM_MODEL=chosen-model\n"
        f"{prefix}_LLM_BASE_URL=https://model.example.test/v1\n{prefix}_LLM_TIMEOUT_S=19\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=dotenv)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    client = module.get_llm()
    assert client.model == "chosen-model"
    assert client.base_url == "https://model.example.test/v1"
    assert client.timeout_s == 19
    assert "local-secret-for-test" not in repr(settings) + repr(client)
    monkeypatch.setenv(f"{prefix}_LLM_MODEL", "environment-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "environment-test-key")
    client = module.get_llm()
    assert client.model == "environment-model"
    actual_key = client._client.api_key if module is qa_llm else client._key
    assert actual_key == "environment-test-key"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    if module is qa_llm:
        with pytest.raises(qa_llm.QaLlmUnavailable):
            module.get_llm()
    else:
        assert module.get_llm() is None
