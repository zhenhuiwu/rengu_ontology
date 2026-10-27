"""Explicit developer-DB SELECT verification; no seeds, migrations, HTTP, or model calls."""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


async def main():
    raw = dotenv_values(BACKEND / ".env").get("RENGU_DATABASE_URL")
    url = make_url(raw or "")
    assert (
        url.host in ("localhost", "127.0.0.1", "::1")
        and url.port == 5433
        and url.database == "rengu_ontology"
    )
    os.environ.update(
        RENGU_DATABASE_URL=raw, RENGU_OBJECT_READ_ENABLED="0", DASHSCOPE_API_KEY="", OPENAI_API_KEY=""
    )
    import app.db.models  # noqa: F401
    from app.common.auth import resolve_user
    from app.common.ontology import set_ontology_scope
    from app.modules.qa.explore_api import ExploreIn, _bind

    engine = create_async_engine(
        url,
        poolclass=NullPool,
        echo=False,
        connect_args={
            "server_settings": {"default_transaction_read_only": "on", "statement_timeout": "10000"}
        },
        execution_options={"postgresql_readonly": True},
    )
    try:
        async with AsyncSession(engine) as session:
            await set_ontology_scope(session, 1)
            assert await session.scalar(text("SHOW transaction_read_only")) == "on"
            assert await session.scalar(text("SELECT current_database()")) == "rengu_ontology"
            actor = await resolve_user(session, "U010001")
            _, bound = await _bind(session, "order")
            assert bound is not None and bound.source is None
            results = {}
            for label, query in (
                ("all", ExploreIn()),
                ("京东到家", ExploreIn(filters={"channel": ["京东到家"]})),
            ):
                rows = bound.where(await bound.stmt(actor), query).subquery()
                column = rows.c.paid_amount
                row = (
                    await session.execute(
                        select(
                            func.count(),
                            func.sum(column),
                            func.avg(column),
                            func.min(column),
                            func.max(column),
                        ).select_from(rows)
                    )
                ).one()
                results[label] = dict(
                    zip(("count", "sum", "avg", "min", "max"), (str(value) for value in row), strict=True)
                )
            print(json.dumps(results, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except Exception as error:
        print(
            f"Read-only verification failed: {type(error).__name__}; no credentials printed", file=sys.stderr
        )
        raise SystemExit(1) from None
