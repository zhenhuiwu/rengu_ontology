"""Explicit isolated UI verification: preserve earlier QA fixtures, append owned records, no HTTP egress."""

import argparse
import asyncio
import json
import os
import sys

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from scripts.fixes_admin_ui_prepare import DATABASE, prepare
from scripts.qa_use_test_server import BACKEND, application, configure


async def verify_existing(target):
    assert target.database == DATABASE
    connection = await asyncpg.connect(
        target.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        async with connection.transaction(readonly=True):
            assert await connection.fetchval("SELECT current_database()") == DATABASE
            assert await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_auth_members m
                JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles u ON u.oid=m.member
                WHERE r.rolname='rengu_ontology_scoped' AND u.rolname=current_user)""")
    finally:
        await connection.close()


async def append_fixtures(target):
    from app.common.ontology import set_ontology_scope
    from app.modules.dynamic.models import ActionRun
    from app.modules.qa.models import QaPage, QaTemplate

    engine = create_async_engine(target, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await set_ontology_scope(session, 1)

            async def baseline():
                pages = list(
                    await session.execute(
                        select(QaPage.code, QaPage.name)
                        .where(QaPage.code.in_(["P906", "P907", "P908", "P909"]))
                        .order_by(QaPage.code)
                    )
                )
                templates = list(
                    await session.execute(
                        select(QaTemplate.code, QaTemplate.name).where(
                            QaTemplate.name == "UICHECK-门店模板-01"
                        )
                    )
                )
                runs = list(
                    await session.execute(
                        select(ActionRun.code, ActionRun.status, ActionRun.params)
                        .where(ActionRun.code.in_(["ACT-20261025-0001", "ACT-20261025-0002"]))
                        .order_by(ActionRun.code)
                    )
                )
                assert len(pages) == 4 and len(templates) == 1 and len(runs) == 2
                return pages, templates, runs

            original = await baseline()
            result = await prepare(session)
            assert await baseline() == original
            await session.commit()
            print(
                json.dumps(
                    {
                        **result,
                        "priorPages": 4,
                        "priorTemplates": 1,
                        "priorLocalRuns": 2,
                        "externalEnabled": False,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    target = configure()
    asyncio.run(verify_existing(target))
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = target.render_as_string(hide_password=False)
    command.upgrade(cfg, "head")
    app = application(target, fixes_profile=True)
    asyncio.run(append_fixtures(target))
    if not args.prepare_only:
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
