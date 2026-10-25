"""命令行：`uv run python -m app.cli seed --help`。"""

import asyncio
from typing import Annotated

import typer

from app.core.config import get_settings
from app.core.logging import setup_logging

cli = typer.Typer(no_args_is_help=True, help="rengu_ontology 后台命令")


@cli.callback()
def _main() -> None:
    setup_logging(get_settings().log_level)


@cli.command()
def seed(
    module: Annotated[list[str] | None, typer.Option("--module", "-m", help="只加载某些模块，可重复")] = None,
    dry_run: Annotated[bool, typer.Option(help="只校验和统计，不写入")] = False,
) -> None:
    """把各模块 seeds/ 下的数据幂等地写入数据库。"""
    from app.db.session import dispose_engine, get_sessionmaker
    from app.modules.versioning.sections import load_sections
    from app.seeds.loader import run_seeds

    load_sections()  # versioning 的 seed 按已登记的 section 组装历史快照

    async def _run() -> None:
        async with get_sessionmaker()() as session:
            reports = await run_seeds(session, module or None, dry_run=dry_run)
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
        await dispose_engine()
        for r in reports:
            typer.echo(f"{r.module:<12} {r.file:<28} → {r.table:<24} {r.rows} 行")
        typer.echo("dry-run，未写入" if dry_run else f"完成：{sum(r.rows for r in reports)} 行")

    asyncio.run(_run())


@cli.command("list-seeds")
def list_seeds() -> None:
    """列出注册的 seed。"""
    from app.seeds.loader import module_specs
    from app.seeds.registry import SEED_MODULES

    for m in SEED_MODULES:
        _, specs = module_specs(m)
        for s in specs:
            typer.echo(f"{m:<12} {s.file:<28} → {s.model.__table__.name} key={','.join(s.key)}")  # type: ignore[attr-defined]


if __name__ == "__main__":
    cli()
