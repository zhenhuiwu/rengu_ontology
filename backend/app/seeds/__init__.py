"""统一 seed 机制。

数据来源（团队约定）：
- 前端 mock → JSON 由 **PDE 的一个脚本** 负责：前端仓库 `npm run export-mock`，
  输出到 `backend/seeds/json/<组>/*.json`，组 = export-mock 的分组（EXPORT_GROUPS：
  common / static / dynamic / action / qa / system），不是后端模块名。文件名就是 mock 里的导出名
  （如 static/RELEASES.json）。字段名、取值、状态、编号都原样保留；
  时间保持 mock 里的北京时间原文（如 "2026-10-24 21:17" 或带秒的 "2026-10-25 13:32:06"），
  由 Python 端按 Asia/Shanghai 解析。
- 每个模块用 `JSON_MODULE` 说明默认读哪个组（见 MODULE_GROUP）；个别文件在别的组时，在 SeedSpec.group 指定。
  每个负责人在自己模块的 seeds/__init__.py 里写「导出文件 → 表」的对照。
- 派生 / 函数计算出来的 mock 数据由 export-mock 的 derived 配置导出到 `<组>/derived/<函数名>.json`，
  格式是 `[{"args": [...], "result": [...] | {...}}]`（每次调用一条）。SeedSpec.file 写成
  "derived/xxx.json" 时 loader 自动展开：result 是数组就逐项成行，是对象就整条成行；每行附带
  `__args__`（调用参数，`{"ref":..., "id": X}` 形式的引用解析成 X），transform 用 `derived_args(row)` 取，
  写库前 loader 会去掉 `__args__`。
- 同一个派生文件在两个组都有时只认一个组（DERIVED_OWNER；目前 dsTables 认 system/derived）。
- 后端不写任何 mock 抽取逻辑，也不手改这些 JSON。
- 前端 mock 里没有、但后端需要的补充行（例如只为满足外键的 repeat_sales 指标）放
  `backend/seeds/extra/<module>/*.json`，SeedSpec 里 `source="extra"`。
- 每个模块先加载 json/，再加载 extra/；同一事务内完成。被 extra 行满足的外键要声明
  `DEFERRABLE INITIALLY DEFERRED`，提交时才检查。
- 表带 is_seed 列（MutableTableMixin / AppendOnlyTableMixin 都含）时，loader 自动写 is_seed=true。
- 按业务键（code / ver / api_name 等唯一列）UPSERT，重跑幂等；代理主键 id 由数据库生成。
- 外键指向代理键 id，mock 里写的是编号：SeedSpec.refs = {"外键列": Ref(目标模型, "目标业务键列")}，
  transform 输出编号，loader 在写库前按 `目标业务键 → id` 换成 id（找不到就报错，不静默丢）。

每个模块在 `app/modules/<模块>/seeds/__init__.py` 里声明：
- `JSON_MODULE`：读 `seeds/json/` 下哪个组（必须是 EXPORT_GROUPS 之一，对照见 MODULE_GROUP）；
- `SEEDS: list[SeedSpec]`：每个 json 落到哪张表、按哪个自然键 upsert、字段怎么转换。

加载：`make seed` / `uv run python -m app.cli seed [-m versioning] [--dry-run]`。
按自然键 UPSERT，重复执行结果不变（幂等）；不删除库里多出来的行。
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import DeclarativeBase

Row = dict[str, Any]

# export-mock 的输出分组（seeds/json/<组>/）
EXPORT_GROUPS = ("common", "static", "dynamic", "action", "qa", "system")

# 后端模块 → 默认读的 export 组（PDE 定：admin/data/integration→system，event→action，biz→common，
# versioning→static）
MODULE_GROUP = {
    "versioning": "static",
    "static": "static",
    "dynamic": "dynamic",
    "action": "action",
    "event": "action",
    "qa": "qa",
    "admin": "system",
    "data": "system",
    "integration": "system",
    "biz": "common",
}

# 两个组都导出的派生文件，只认这里写的组
# （export-mock 里 static/derived 和 system/derived 的 dsTables 内容相同）
DERIVED_OWNER = {"derived/dsTables.json": "system"}

ARGS_KEY = "__args__"


@dataclass(frozen=True)
class Ref:
    """外键引用：transform 输出业务编号，loader 换成目标表的代理键 id。"""

    model: type[DeclarativeBase]
    # 目标表的业务键列（code / ver / api_name …）；复合业务键写元组，transform 输出同样长度的元组
    key: str | tuple[str, ...] = "code"
    nullable: bool = True  # 编号为 None 时外键写 None


@dataclass(frozen=True)
class SeedSpec:
    model: type[DeclarativeBase]
    file: str  # 相对 seeds/json/<组>/，如 "RELEASES.json"、"derived/runsOf.json"
    key: tuple[str, ...]  # upsert 冲突键（业务键列名，如 ("code",)、("ver",)；不是代理主键 id）
    # 返回 None 跳过该行；返回 list 一行展开成多行（如订单 → 订单行）
    transform: Callable[[Row, "SeedContext"], Row | list[Row] | None] = lambda r, _: r
    path: str | None = None  # JSON 顶层是对象时，取哪个键下的数组
    source: Literal["json", "extra"] = "json"  # seeds/json/<module>/ 或 seeds/extra/<module>/
    description: str = ""
    group: str | None = None  # 文件不在本模块默认组时指定（如 dynamic 读 action/derived/runsOf.json）
    derived: bool | None = None  # None = 按路径判断（derived/ 开头即按 [{args, result}] 展开）
    refs: dict[str, Ref] = field(default_factory=dict)  # 外键列 → 引用（编号 → id）

    @property
    def is_derived(self) -> bool:
        return self.derived if self.derived is not None else self.file.startswith("derived/")


@dataclass
class SeedContext:
    """transform 里可以读同组其它 JSON（例如关联数据）；`group=` 读别的组。"""

    json_dir: Path
    cache: dict[str, Any] = field(default_factory=dict)
    root: Path | None = None  # seeds/json/（或 seeds/extra/），读别的组时用

    def load(self, file: str, path: str | None = None, *, group: str | None = None) -> Any:
        base = (self.root or self.json_dir.parent) / group if group else self.json_dir
        cache_key = f"{group or ''}:{file}"
        if cache_key not in self.cache:
            self.cache[cache_key] = json.loads((base / file).read_text(encoding="utf-8"))
        data = self.cache[cache_key]
        return data[path] if path else data


def rows_of(data: Any) -> Iterable[Row]:
    if isinstance(data, list):
        return data
    raise ValueError("seed 数据必须是 JSON 数组；顶层是对象时在 SeedSpec.path 指定键")


def resolve_arg(arg: Any) -> Any:
    """derived 的参数：`{"ref": "dynamic/RULE_DEFS", "key": "id", "id": "R-007"}` → "R-007"；
    带 field 的引用取 field 对应的值（没有就退回 id）；普通值原样返回。"""
    if isinstance(arg, dict) and "ref" in arg:
        return (
            arg.get(arg["field"], arg.get("id"))
            if "field" in arg
            else arg.get("id", arg.get(arg.get("key", "id")))
        )
    return arg


def derived_rows(data: Any) -> Iterable[Row]:
    """展开 export-mock 的派生文件 `[{"args": [...], "result": [...] | {...} | null}]`。"""
    if not isinstance(data, list):
        raise ValueError("派生文件必须是 [{args, result}] 数组")
    for call in data:
        if not isinstance(call, dict) or "result" not in call:
            raise ValueError("派生文件的每一项必须有 args 和 result")
        args = [resolve_arg(a) for a in call.get("args", [])]
        result = call["result"]
        items = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"派生结果的元素必须是对象：{item!r}")
            yield {**item, ARGS_KEY: args}


def derived_args(row: Row) -> list[Any]:
    """transform 里取派生行对应的调用参数（引用已解析成 id）。"""
    return row.get(ARGS_KEY, [])


def fill_timeline_dates(items: list[dict[str, Any]], field: str = "time") -> list[datetime]:
    """mock 时间线：只有第一行带日期（"2026-10-25 13:32:06"），后面只有 "HH:mm:ss" / "HH:mm"。

    返回每行完整的带时区时间：沿用上一行的日期；时间比上一行早（跨过午夜）→ 日期 +1 天。
    中途某行自带日期则以它为准。通用：动作 / 事件 / 规则的时间线 seed 都用它。
    """
    from app.core.schema import parse_display_dt

    out: list[datetime] = []
    prev: datetime | None = None
    for it in items:
        raw = str(it[field]).strip()
        if len(raw) >= 16 and raw[10] == " ":
            cur = parse_display_dt(raw)
        else:
            if prev is None:
                raise ValueError(f"时间线第一行没有日期：{raw!r}")
            parts = [int(x) for x in raw.split(":")]
            h, m, s = [*parts, 0][:3]
            cur = prev.replace(hour=h, minute=m, second=s, microsecond=0)
            if cur < prev:
                cur += timedelta(days=1)
        out.append(cur)
        prev = cur
    return out
