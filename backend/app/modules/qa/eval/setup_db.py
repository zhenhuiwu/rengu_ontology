"""重建评测库：DROP / CREATE → alembic upgrade head → 灌全部种子。"""

import asyncio

from app.modules.qa.eval import setup_env


async def main() -> None:
    url = setup_env()
    import asyncpg
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.seeds.loader import run_seeds
    from app.seeds.registry import SEED_MODULES

    u = make_url(url)
    admin = u.set(drivername="postgresql", database="rengu").render_as_string(hide_password=False)
    c = await asyncpg.connect(admin)
    await c.execute(f'DROP DATABASE IF EXISTS "{u.database}" WITH (FORCE)')
    await c.execute(f'CREATE DATABASE "{u.database}"')
    await c.close()
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    await asyncio.to_thread(command.upgrade, cfg, "head")
    eng = create_async_engine(url)
    async with AsyncSession(eng) as s:
        await run_seeds(s, SEED_MODULES)
        await s.commit()
    await eng.dispose()
    print("seeded", u.database)


if __name__ == "__main__":
    asyncio.run(main())
