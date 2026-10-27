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

    from app.common.ontology import DEFAULT_ONTOLOGY_ID, set_ontology_scope
    from app.modules.versioning.sections import load_sections
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
    load_sections()  # 与 `app.cli seed` 一致：versioning 按已登记 section 组装历史快照
    eng = create_async_engine(url)
    async with AsyncSession(eng) as s:
        # ontology_0018 起领域表强制 RLS：种子落在默认工作空间（与 tests/conftest 一致）。
        await set_ontology_scope(s, DEFAULT_ONTOLOGY_ID)
        await run_seeds(s, SEED_MODULES)
        await _bind_members(s)
        await s.commit()
    await eng.dispose()
    print("seeded", u.database)


async def _bind_members(s) -> None:
    """种子不写 ontology_member：照 ontology_0018 对存量用户的映射把用户 / 角色挂到空间 1，
    否则 resolve_user 在空间 1 下 403（与 tests/helpers/workspace.bind_workspace_members 同口径）。"""
    from sqlalchemy import text

    await s.execute(
        text("""
        INSERT INTO ontology_member (ontology_id,user_id,role)
        SELECT 1,u.id,CASE WHEN EXISTS (
            SELECT 1 FROM sys_role_member m JOIN sys_role r ON r.id=m.role_id
            WHERE m.user_id=u.id AND r.func->>'releases'='manage') THEN 'owner'
        WHEN EXISTS (SELECT 1 FROM sys_role_member m JOIN sys_role r ON r.id=m.role_id,
            jsonb_each_text(r.func) f WHERE m.user_id=u.id AND f.value='manage') THEN 'editor'
        ELSE 'viewer' END FROM sys_user u
        ON CONFLICT (ontology_id,user_id) DO UPDATE SET role=excluded.role
    """)
    )
    await s.execute(
        text("""
        INSERT INTO ontology_role_binding (ontology_id,role_id) SELECT 1,id FROM sys_role
        ON CONFLICT (ontology_id,role_id) DO NOTHING
    """)
    )


if __name__ == "__main__":
    asyncio.run(main())
