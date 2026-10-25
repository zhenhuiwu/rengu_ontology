"""统一 seed 机制。

数据来源（团队约定）：
- 前端 mock → JSON 由 **PDE 的一个脚本** 负责：前端仓库 `npm run export-mock`，
  输出到 `backend/seeds/json/<module>/*.json`。字段名、取值、状态、编号都原样保留；
  时间保持 mock 里的北京时间原文（如 "2026-10-24 21:17"），由 Python 端按 Asia/Shanghai 解析。
- 派生 / 函数计算出来的 mock 数据（例如用 seeded() 生成的列表）由 export-mock 的 derived 配置导出到
  `backend/seeds/json/<module>/derived/*.json`；SeedSpec.file 写成 "derived/xxx.json" 即可。
- 后端不写任何 mock 抽取逻辑，也不手改这些 JSON。
- 前端 mock 里没有、但后端需要的补充行（例如只为满足外键的 repeat_sales 指标）放
  `backend/seeds/extra/<module>/*.json`，SeedSpec 里 `source="extra"`。
- 每个模块先加载 json/，再加载 extra/；同一事务内完成。被 extra 行满足的外键要声明
  `DEFERRABLE INITIALLY DEFERRED`，提交时才检查。
- 表带 is_seed 列（SeedMarkMixin）时，loader 自动写 is_seed=true。
- 按业务键（自然键）UPSERT，重跑幂等。

每个模块在 `app/modules/<模块>/seeds/__init__.py` 里声明：
- `JSON_MODULE`（可选）：读 `seeds/json/` 下哪个子目录，默认等于模块名；
- `SEEDS: list[SeedSpec]`：每个 json 落到哪张表、按哪个自然键 upsert、字段怎么转换。

加载：`make seed` / `uv run python -m app.cli seed [-m versioning] [--dry-run]`。
按自然键 UPSERT，重复执行结果不变（幂等）；不删除库里多出来的行。
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import DeclarativeBase

Row = dict[str, Any]


@dataclass(frozen=True)
class SeedSpec:
    model: type[DeclarativeBase]
    file: str  # 相对 seeds/json/<module>/
    key: tuple[str, ...]  # upsert 冲突键（数据库列名）
    transform: Callable[[Row, "SeedContext"], Row | None] = lambda r, _: r  # 返回 None 跳过该行
    path: str | None = None  # JSON 顶层是对象时，取哪个键下的数组
    source: Literal["json", "extra"] = "json"  # seeds/json/<module>/ 或 seeds/extra/<module>/
    description: str = ""


@dataclass
class SeedContext:
    """transform 里可以读同目录其它 JSON（例如关联数据）。"""

    json_dir: Path
    cache: dict[str, Any] = field(default_factory=dict)

    def load(self, file: str, path: str | None = None) -> Any:
        if file not in self.cache:
            self.cache[file] = json.loads((self.json_dir / file).read_text(encoding="utf-8"))
        data = self.cache[file]
        return data[path] if path else data


def rows_of(data: Any) -> Iterable[Row]:
    if isinstance(data, list):
        return data
    raise ValueError("seed 数据必须是 JSON 数组；顶层是对象时在 SeedSpec.path 指定键")
