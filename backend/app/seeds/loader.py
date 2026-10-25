import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.dialects import get_adapter
from app.seeds import (
    ARGS_KEY,
    DERIVED_OWNER,
    EXPORT_GROUPS,
    Ref,
    SeedContext,
    SeedSpec,
    derived_rows,
    rows_of,
)
from app.seeds.registry import SEED_MODULES

_log = logging.getLogger("app.seeds")


@dataclass
class SeedReport:
    module: str
    file: str
    table: str
    rows: int


def module_group(module: str) -> str:
    """模块默认读的 export 组（模块 seeds 包里的 JSON_MODULE）。"""
    pkg = importlib.import_module(f"app.modules.{module}.seeds")
    group = getattr(pkg, "JSON_MODULE", module)
    if group not in EXPORT_GROUPS:
        raise ValueError(f"{module}.JSON_MODULE={group!r} 不是 export-mock 的分组 {EXPORT_GROUPS}")
    return group


def module_specs(module: str, json_root: Path | None = None) -> tuple[Path, list[SeedSpec]]:
    pkg = importlib.import_module(f"app.modules.{module}.seeds")
    root = json_root or get_settings().seeds_json_dir
    return root / module_group(module), list(getattr(pkg, "SEEDS", []))


def spec_group(module: str, spec: SeedSpec) -> str:
    group = spec.group or module_group(module)
    if spec.source == "json":
        if group not in EXPORT_GROUPS:
            raise ValueError(f"{module}: SeedSpec.group={group!r} 不是 export-mock 的分组")
        owner = DERIVED_OWNER.get(spec.file)
        if owner and owner != group:
            raise ValueError(f"{module}: {spec.file} 以 {owner}/ 下的为准，不要读 {group}/")
    return group


def _load_rows(ctx: SeedContext, spec: SeedSpec) -> list[dict]:
    data = ctx.load(spec.file, spec.path)
    raw = derived_rows(data) if spec.is_derived else rows_of(data)
    out = []
    for x in raw:
        r = spec.transform(dict(x), ctx)
        if r:
            r = {k: v for k, v in r.items() if k != ARGS_KEY}
            out.append(r)
    return out


async def resolve_refs(session: AsyncSession, rows: list[dict], refs: dict[str, Ref]) -> list[dict]:
    """把外键列里的业务编号换成目标表的代理键 id（一列一次查询）。"""
    for col, ref in refs.items():
        codes = {r[col] for r in rows if r.get(col) is not None}
        if not codes:
            continue
        key_col = getattr(ref.model, ref.key)
        found = dict((await session.execute(select(key_col, ref.model.id).where(key_col.in_(codes)))).all())  # type: ignore[attr-defined]
        missing = codes - found.keys()
        if missing:
            table = ref.model.__table__.name  # type: ignore[attr-defined]
            raise ValueError(f"seed 外键 {col} → {table}.{ref.key} 找不到：{sorted(missing)[:10]}")
        for r in rows:
            v = r.get(col)
            if v is None and not ref.nullable:
                raise ValueError(f"seed 外键 {col} 不能为空")
            r[col] = found[v] if v is not None else None
    return rows


async def _upsert_resolving(session: AsyncSession, adapter: Any, spec: SeedSpec, rows: list[dict]) -> int:
    """外键编号 → id 后 upsert。自引用（如 ontology_release.parent_id）按层写入：被引用的行先写，
    下一层才能解析到它的 id（只追加表不能先写 null 再补）。"""
    self_cols = {c for c, ref in spec.refs.items() if ref.model is spec.model}
    other = {c: ref for c, ref in spec.refs.items() if c not in self_cols}
    rows = await resolve_refs(session, rows, other)
    if not self_cols:
        return await adapter.upsert(session, spec.model, rows, spec.key)
    pending, total = rows, 0
    while pending:
        # 还没写入的行的业务键值；引用它们的行要等下一层
        waiting = {c: {r.get(spec.refs[c].key) for r in pending} for c in self_cols}
        ready = [r for r in pending if all(r.get(c) is None or r[c] not in waiting[c] for c in self_cols)]
        if not ready:
            raise ValueError(f"seed 自引用成环：{spec.model.__table__.name}")  # type: ignore[attr-defined]
        self_refs = {c: spec.refs[c] for c in self_cols}
        total += await adapter.upsert(
            session, spec.model, await resolve_refs(session, ready, self_refs), spec.key
        )
        done = {id(r) for r in ready}
        pending = [r for r in pending if id(r) not in done]
    return total


async def run_seeds(
    session: AsyncSession,
    modules: list[str] | None = None,
    *,
    dry_run: bool = False,
    json_root: Path | None = None,
    extra_root: Path | None = None,
) -> list[SeedReport]:
    """在给定会话里执行（不 commit，由调用方提交；dry_run 时调用方回滚）。"""
    reports: list[SeedReport] = []
    adapter = get_adapter(session)
    for module in modules or SEED_MODULES:
        _, specs = module_specs(module)
        s = get_settings()
        roots = {"json": json_root or s.seeds_json_dir, "extra": extra_root or s.seeds_extra_dir}
        ctxs: dict[tuple[str, str], SeedContext] = {}
        # 先 json/（前端 mock 导出），再 extra/（后端补充）
        for spec in sorted(specs, key=lambda x: x.source != "json"):
            group = spec_group(module, spec)
            ctx = ctxs.setdefault(
                (spec.source, group), SeedContext(roots[spec.source] / group, root=roots[spec.source])
            )
            if not (ctx.json_dir / spec.file).exists():
                _log.warning("seed 文件缺失，跳过：%s（先跑 npm run export-mock）", ctx.json_dir / spec.file)
                continue
            rows = _load_rows(ctx, spec)
            if "is_seed" in spec.model.__table__.c:  # type: ignore[attr-defined]
                rows = [{**r, "is_seed": True} for r in rows]
            n = await _upsert_resolving(session, adapter, spec, rows)
            table = spec.model.__table__.name  # type: ignore[attr-defined]
            reports.append(SeedReport(module, f"{spec.source}/{group}/{spec.file}", table, n))
            suffix = "（dry-run）" if dry_run else ""
            _log.info(
                "seed %s ← %s/%s/%s → %s: %d 行%s", module, spec.source, group, spec.file, table, n, suffix
            )
    return reports
