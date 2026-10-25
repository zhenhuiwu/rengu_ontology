"""全局配置：统一从环境变量 / .env 读取（前缀 RENGU_）。"""

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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

    # 全站“数据截至”（前端 DATA_CUTOFF）。以后由数据同步任务写入，这里只是兜底值。
    data_cutoff: str = "2026-10-24 23:00"
    # 业务时区：所有对外展示的时间都按这个时区格式化
    display_tz: str = "Asia/Shanghai"
    # 固定“现在”（见 app/core/clock.py）。非 prod 环境不配置时默认用 DEV_FIXED_NOW；prod 不配置 = 真实时间
    fixed_now: datetime | None = None

    # 开发期身份桩：PDE 的鉴权实现上线前，没有身份时按这个用户处理（仅 dev/test 生效）
    # dev/test 默认身份（请求头 X-Demo-As 可覆盖；prod 不生效）
    dev_user: str = Field(default="王敏")

    # seed 数据目录：PDE 的 `npm run export-mock` 输出到 backend/seeds/json/<module>/
    seeds_json_dir: Path = Path(__file__).resolve().parents[2] / "seeds" / "json"
    # 后端补充的 seed（mock 里没有、只为满足外键等）：backend/seeds/extra/<module>/，is_seed=true
    seeds_extra_dir: Path = Path(__file__).resolve().parents[2] / "seeds" / "extra"

    # auto-ontology 问数底座（独立服务，只在内网，由我们的网关转发）
    auto_ontology_url: str | None = None

    @property
    def effective_fixed_now(self) -> datetime | None:
        if self.fixed_now is not None:
            return self.fixed_now
        return None if self.env == "prod" else DEV_FIXED_NOW


# 前端 mock 的“现在”：2026-10-25 15:42 北京时间
DEV_FIXED_NOW = datetime.fromisoformat("2026-10-25T15:42:00+08:00")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def api_prefix(module: str) -> str:
    """模块接口前缀，例如 api_prefix("ontology") == "/api/ontology/v1"。"""
    return get_settings().api_prefix_template.format(module=module)
