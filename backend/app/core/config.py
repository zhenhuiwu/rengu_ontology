"""全局配置：统一从环境变量 / .env 读取（前缀 RENGU_）。"""

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RENGU_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    app_name: str = "rengu_ontology backend"
    # 接口前缀按模块：/api/<module>/v1（例如 /api/ontology/v1、/api/qa/v1），见 app/api.py
    api_prefix_template: str = "/api/{module}/v1"
    log_level: str = "INFO"

    # 元数据库（本系统自己的库）。PostgreSQL 先行；达梦/金仓以后通过 app/db/dialects 适配。
    database_url: str = "postgresql+asyncpg://rengu:rengu@127.0.0.1:5440/rengu"
    db_echo: bool = False
    db_pool_size: int = 10
    # 兼容现有模型环境变量，也可从本项目被忽略的 .env 读取。
    llm_api_key: SecretStr | None = Field(None, validation_alias="DASHSCOPE_API_KEY")
    qa_llm_model: str | None = Field(None, validation_alias="QA_LLM_MODEL")
    qa_llm_base_url: str | None = Field(None, validation_alias="QA_LLM_BASE_URL")
    qa_llm_timeout_s: float | None = Field(None, validation_alias="QA_LLM_TIMEOUT_S")
    auto_llm_model: str | None = Field(None, validation_alias="AUTO_ONTOLOGY_LLM_MODEL")
    auto_llm_base_url: str | None = Field(None, validation_alias="AUTO_ONTOLOGY_LLM_BASE_URL")
    auto_llm_timeout_s: float | None = Field(None, validation_alias="AUTO_ONTOLOGY_LLM_TIMEOUT_S")
    # 内部事件接收凭证；缺配置时端点不可用，绝不使用开发期默认用户放行。
    internal_event_token: SecretStr | None = None
    # JSON workspace-id -> token; the legacy token authorizes workspace 1 only.
    internal_event_tokens: SecretStr | None = None
    rule_replay_signing_key: SecretStr | None = None
    # Explicit opt-in plus per-system HTTP paths/credentials; only put real values in ignored .env.
    integration_real_connect: bool = False
    integration_adapters: SecretStr | None = None
    integration_ca_file: Path | None = None
    integration_local_demo: str | None = None
    event_delivery_http_enabled: bool = False
    event_delivery_routes: SecretStr | None = None
    # 问数 NL2SQL 执行用的只读库账号（api-index §1.4 硬规则；库层 default_transaction_read_only=on、
    # 只授权业务表）。不配置时网关拒绝执行，不回退到 database_url。
    qa_readonly_database_url: str | None = None
    # 只读引擎每个连接的 statement_timeout（毫秒），连接时设置（RENGU_QA_STATEMENT_TIMEOUT_MS）
    qa_statement_timeout_ms: int = 15000

    # 全站“数据截至”（前端 DATA_CUTOFF）。以后由数据同步任务写入，这里只是兜底值。
    data_cutoff: str = "2026-10-24 23:00"
    # 业务时区：所有对外展示的时间都按这个时区格式化
    display_tz: str = "Asia/Shanghai"
    # 仅 test 默认固定时间；开发服务默认真实时间，种子重放可显式冻结。
    fixed_now: datetime | None = None

    # 开发期身份桩：PDE 的鉴权实现上线前，没有身份时按这个用户处理（仅 dev/test 生效）
    # dev/test 默认身份（请求头 X-Demo-As 可覆盖；prod 不生效）
    # 值是用户编号 sys_user.code（前端 userId），不是姓名。U010001 = 王敏（演示身份 admin）
    dev_user: str = Field(default="U010001")
    # 桩鉴权里视为本体管理员的用户编号（mock ONTOLOGY_ADMINS = 王敏）。PDE 接入真实权限后删除
    demo_admin_codes: list[str] = Field(default_factory=lambda: ["U010001"])

    # seed 数据目录：PDE 的 `npm run export-mock` 输出到 backend/seeds/json/<module>/
    seeds_json_dir: Path = Path(__file__).resolve().parents[2] / "seeds" / "json"
    # 后端补充的 seed（mock 里没有、只为满足外键等）：backend/seeds/extra/<module>/，is_seed=true
    seeds_extra_dir: Path = Path(__file__).resolve().parents[2] / "seeds" / "extra"

    # 快照 section 自动发现的包（load_sections 扫描 <包>.<模块>.sections）。只有测试会追加别的包
    section_packages: list[str] = Field(default_factory=lambda: ["app.modules"])

    # auto-ontology 问数底座（独立服务，只在内网，由我们的网关转发）
    auto_ontology_url: str | None = None

    @property
    def effective_fixed_now(self) -> datetime | None:
        if self.fixed_now is not None:
            return self.fixed_now
        return DEV_FIXED_NOW if self.env == "test" else None


# 前端 mock 的“现在”：2026-10-25 15:42 北京时间
DEV_FIXED_NOW = datetime.fromisoformat("2026-10-25T15:42:00+08:00")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def api_prefix(module: str) -> str:
    """模块接口前缀，例如 api_prefix("ontology") == "/api/ontology/v1"。"""
    return get_settings().api_prefix_template.format(module=module)
