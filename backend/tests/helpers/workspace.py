"""测试公用：当前测试库名与零售空间 1 的成员映射（只用于测试库）。"""

import os

from sqlalchemy import text

# 与 tests/conftest.py 相同的约定：RENGU_TEST_DB 未设置时用 rengu_test。
TEST_DB = os.environ.get("RENGU_TEST_DB", "rengu_test")


async def bind_workspace_members(session) -> None:
    """照 ontology_0018 迁移对存量用户的映射，把已灌入的用户和角色挂到空间 1（幂等）。"""
    assert session.get_bind().engine.url.database == TEST_DB
    await session.execute(
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
    await session.execute(
        text("""
        INSERT INTO ontology_role_binding (ontology_id,role_id) SELECT 1,id FROM sys_role
        ON CONFLICT (ontology_id,role_id) DO NOTHING
    """)
    )
