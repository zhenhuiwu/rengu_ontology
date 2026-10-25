import importlib
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.dialects import get_adapter
from app.seeds import SeedContext, SeedSpec, rows_of
from app.seeds.registry import SEED_MODULES

_log = logging.getLogger("app.seeds")


@dataclass
class SeedReport:
    module: str
    file: str
    table: str
    rows: int


def module_specs(module: str, json_root: Path | None = None) -> tuple[Path, list[SeedSpec]]:
    pkg = importlib.import_module(f"app.modules.{module}.seeds")
    root = json_root or get_settings().seeds_json_dir
    return root / getattr(pkg, "JSON_MODULE", module), list(getattr(pkg, "SEEDS", []))


def _dirs(module: str, json_root: Path | None, extra_root: Path | None) -> dict[str, Path]:
    pkg = importlib.import_module(f"app.modules.{module}.seeds")
    sub = getattr(pkg, "JSON_MODULE", module)
    s = get_settings()
    return {"json": (json_root or s.seeds_json_dir) / sub, "extra": (extra_root or s.seeds_extra_dir) / sub}


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
        dirs = _dirs(module, json_root, extra_root)
        ctxs = {k: SeedContext(d) for k, d in dirs.items()}
        # 先 json/（前端 mock 导出），再 extra/（后端补充）
        for spec in sorted(specs, key=lambda x: x.source != "json"):
            ctx = ctxs[spec.source]
            if not (ctx.json_dir / spec.file).exists():
                _log.warning("seed 文件缺失，跳过：%s（先跑 npm run export-mock）", ctx.json_dir / spec.file)
                continue
            raw = rows_of(ctx.load(spec.file, spec.path))
            rows = [r for r in (spec.transform(dict(x), ctx) for x in raw) if r]
            if "is_seed" in spec.model.__table__.c:  # type: ignore[attr-defined]
                rows = [{**r, "is_seed": True} for r in rows]
            n = await adapter.upsert(session, spec.model, rows, spec.key)
            table = spec.model.__table__.name  # type: ignore[attr-defined]
            reports.append(SeedReport(module, f"{spec.source}/{spec.file}", table, n))
            suffix = "（dry-run）" if dry_run else ""
            _log.info("seed %s/%s/%s → %s: %d 行%s", module, spec.source, spec.file, table, n, suffix)
    return reports
