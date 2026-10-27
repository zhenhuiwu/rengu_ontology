"""Run the three use-area test groups only against their explicitly isolated local databases."""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

DATABASES = {f"rengu_test_use_{group}_20261027" for group in ("objects", "qa", "ops")}
BACKEND = Path(__file__).resolve().parents[1]


async def prepare(source, name):
    admin = await asyncpg.connect(source.set(drivername="postgresql").render_as_string(hide_password=False))
    try:
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name):
            await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    target = await asyncpg.connect(
        source.set(drivername="postgresql", database=name).render_as_string(hide_password=False)
    )
    try:
        direct = await target.fetchval("""SELECT EXISTS (
            SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
            JOIN pg_roles u ON u.oid=m.member
            WHERE r.rolname='rengu_ontology_scoped' AND u.rolname=current_user)""")
        if not direct:
            raise RuntimeError(
                "Existing direct runtime-role membership is absent; "
                "migration must not grant it to the source login"
            )
    finally:
        await target.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", choices=sorted(DATABASES), required=True)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        raw = dotenv_values(BACKEND / ".env").get("RENGU_DATABASE_URL")
        source = make_url(raw or "")
        if (
            source.host not in ("localhost", "127.0.0.1", "::1")
            or source.port != 5433
            or source.database != "rengu_ontology"
        ):
            raise RuntimeError("Expected the ignored local 5433 rengu_ontology source configuration")
        asyncio.run(prepare(source, args.db))
    except Exception as error:
        print(
            f"Isolated test preparation refused: {type(error).__name__}; no credentials printed",
            file=sys.stderr,
        )
        return 2
    env = os.environ.copy()
    env.update(
        RENGU_DATABASE_URL=source.render_as_string(hide_password=False),
        RENGU_TEST_DB=args.db,
        RENGU_QA_READONLY_DATABASE_URL=source.set(database=args.db).render_as_string(hide_password=False),
        RENGU_ENV="test",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        PYTHONUNBUFFERED="1",
        DASHSCOPE_API_KEY="",
        OPENAI_API_KEY="",
        AUTO_ONTOLOGY_LLM_API_KEY="",
        RENGU_INTEGRATION_REAL_CONNECT="false",
        RENGU_EVENT_DELIVERY_HTTP_ENABLED="false",
        RENGU_DS_REAL_CONNECT="0",
        RENGU_OBJECT_READ_ENABLED="0",
        RENGU_OBJECT_READ_BINDINGS="",
    )
    pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    print(
        f"Isolated tests: {args.db}; existing direct runtime-role membership verified; "
        "external execution disabled",
        flush=True,
    )
    return subprocess.run([sys.executable, "-m", "pytest", *pytest_args], cwd=BACKEND, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
