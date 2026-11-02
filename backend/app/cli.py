"""命令行：`uv run python -m app.cli seed --help`。"""

import asyncio
import json
from typing import Annotated

import typer

from app.core.config import get_settings
from app.core.logging import setup_logging

cli = typer.Typer(no_args_is_help=True, help="rengu_ontology 后台命令")


@cli.command("system-init")
def system_init(
    database: Annotated[str, typer.Option("--database", help="必须与配置的目标数据库名完全一致")],
    apply: Annotated[bool, typer.Option("--apply", help="显式写入；默认只读检查")] = False,
) -> None:
    """空白环境初始化：必要账号、组织、权限和标准系统事件；不导入演示业务。"""
    from sqlalchemy.engine import make_url

    from app.db.session import dispose_engine, get_sessionmaker
    from app.seeds.bootstrap import initialize_system

    if database != make_url(get_settings().database_url).database:
        raise typer.BadParameter("目标数据库名与配置不一致；未连接数据库")

    async def _run() -> None:
        try:
            async with get_sessionmaker()() as session:
                plan = await initialize_system(session, apply=apply)
                if apply:
                    await session.commit()
                else:
                    await session.rollback()
            typer.echo(json.dumps(plan, ensure_ascii=False))
        finally:
            await dispose_engine()

    try:
        asyncio.run(_run())
    except Exception:
        raise typer.BadParameter(
            "系统初始化未确认完成；请核查目标库状态、迁移、连接与首次安装条件（不输出凭据）"
        ) from None


@cli.callback()
def _main() -> None:
    setup_logging(get_settings().log_level)


@cli.command()
def seed(
    module: Annotated[list[str] | None, typer.Option("--module", "-m", help="只加载某些模块，可重复")] = None,
    dry_run: Annotated[bool, typer.Option(help="隔离演示库尝试写入后回滚；存量只读审计使用审计脚本")] = False,
    demo: Annotated[bool, typer.Option("--demo", help="显式选择零售演示数据（仅空间 1，禁止生产）")] = False,
) -> None:
    """显式导入零售演示数据；不用于生产系统初始化。"""
    if not demo:
        raise typer.BadParameter("演示数据需要显式 --demo；生产请使用独立系统初始化入口")
    if get_settings().env == "prod":
        raise typer.BadParameter("生产环境禁止演示种子")
    from app.db.session import dispose_engine, get_sessionmaker
    from app.modules.versioning.sections import load_sections
    from app.seeds.loader import run_seeds

    load_sections()  # versioning 的 seed 按已登记的 section 组装历史快照

    async def _run() -> None:
        async with get_sessionmaker()() as session:
            from app.common.ontology import DEFAULT_ONTOLOGY_ID, set_ontology_scope

            await set_ontology_scope(session, DEFAULT_ONTOLOGY_ID)
            reports = await run_seeds(session, module or None, dry_run=dry_run)
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
        await dispose_engine()
        for r in reports:
            typer.echo(f"{r.module:<12} {r.file:<28} → {r.table:<24} {r.rows} 行")
        typer.echo(
            "dry-run，已回滚（序列号可能消耗）" if dry_run else f"完成：{sum(r.rows for r in reports)} 行"
        )

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
