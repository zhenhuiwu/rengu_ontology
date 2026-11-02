"""Consolidate platform configuration; keep every historical identity and business row.

Run without arguments to inspect the plan, then use --apply to save it. Only the
configured rengu_ontology PostgreSQL platform database is accepted.
"""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
NAME = "仁谷科技"


def fingerprint(cursor):
    """Record stable identity, membership and permission anchors, respecting RLS."""
    state = {}
    for table, columns in (
        ("sys_user", "id,code,name,account"),
        ("sys_role", "*"),
        ("sys_role_member", "*"),
        ("ontology_workspace", "*"),
        ("ontology_member", "*"),
    ):
        cursor.execute(f"SELECT {columns} FROM {table} ORDER BY id")
        state[table] = cursor.fetchall()
    cursor.execute("SELECT id FROM ontology_workspace ORDER BY id")
    for workspace in cursor.fetchall():
        cursor.execute("SELECT set_config('rengu.ontology_id', %s, true)", (str(workspace["id"]),))
        cursor.execute("SELECT * FROM sys_role_scope WHERE ontology_id=%s ORDER BY id", (workspace["id"],))
        state[f"scopes:{workspace['id']}"] = cursor.fetchall()
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


def run(apply=False):
    raw = dotenv_values(ROOT / ".env").get("RENGU_DATABASE_URL")
    if not raw:
        raise SystemExit("平台数据库未配置；未作修改")
    url = make_url(raw)
    if url.database != "rengu_ontology" or not url.drivername.startswith("postgresql"):
        raise SystemExit("仅允许 rengu_ontology PostgreSQL 平台库；未作修改")
    with psycopg.connect(host=url.host, port=url.port or 5432, dbname=url.database,
                         user=url.username, password=url.password, connect_timeout=8,
                         row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            if apply:
                cursor.execute("LOCK TABLE sys_user,sys_org,sys_tenant,api_key IN SHARE ROW EXCLUSIVE MODE")
            else:
                cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT id,code,name,account,status FROM sys_user WHERE code='U010001'")
            admin = cursor.fetchone()
            if not admin or (admin["id"], admin["name"], admin["account"], admin["status"]) != (121, "admin", "admin", "active"):
                raise SystemExit("admin 历史身份与预期不符；未作修改")
            cursor.execute("SELECT 1 FROM sys_role_member m JOIN sys_role r ON r.id=m.role_id WHERE m.user_id=121 AND r.func->>'users'='manage' AND r.func->>'roles'='manage'")
            if not cursor.fetchone():
                raise SystemExit("admin 缺少恢复管理权限；未作修改")
            cursor.execute("SELECT w.id FROM ontology_workspace w LEFT JOIN ontology_member m ON m.ontology_id=w.id AND m.user_id=121 WHERE w.deleted_at IS NULL AND m.role IS DISTINCT FROM 'owner'")
            if cursor.fetchall():
                raise SystemExit("存在 admin 不是 owner 的活跃本体；未作修改")
            cursor.execute("SELECT conname FROM pg_constraint WHERE contype='f' AND confrelid IN ('sys_org'::regclass,'sys_tenant'::regclass)")
            if cursor.fetchall():
                raise SystemExit("组织表已被其他表引用，须重新审阅；未作修改")
            # Check all workspaces explicitly: an administrator connection may bypass RLS.
            cursor.execute("""
                SELECT 'rule' AS kind, code AS reference FROM rule
                WHERE status<>'disabled' AND trigger->>'enabled'='true'
                  AND trigger->>'runAsUserId' IS DISTINCT FROM 'U010001'
                UNION ALL SELECT 'rule-run',id::text FROM rule_schedule_run
                WHERE state IN ('pending','running') AND created_by IS DISTINCT FROM 'U010001'
                UNION ALL SELECT 'quality',q.code FROM quality_schedule_cursor c
                JOIN quality_check q ON q.id=c.check_id
                WHERE c.next_at IS NOT NULL AND q.created_by IS DISTINCT FROM 'U010001'
                UNION ALL SELECT 'page',code FROM qa_page
                WHERE next_run IS NOT NULL AND owner_id<>121
                UNION ALL SELECT 'subscription',p.code FROM qa_page_subscription s
                JOIN qa_page p ON p.id=s.page_id WHERE s.enabled AND p.owner_id<>121
            """)
            if cursor.fetchall():
                raise SystemExit("活动执行配置仍依赖其他用户，须先审阅运行身份；未作修改")
            cursor.execute("SELECT * FROM sys_org ORDER BY id")
            orgs = cursor.fetchall()
            if not orgs or {o["code"] for o in orgs} - {"hd", "hn", "hq"} or any(not o["is_seed"] for o in orgs) or not any(o["id"] == 1 and o["code"] == "hd" for o in orgs):
                raise SystemExit("存在未经审阅的组织配置；未作修改")
            cursor.execute("SELECT * FROM sys_tenant ORDER BY id")
            tenants = cursor.fetchall()
            if len(tenants) != 1 or tenants[0]["id"] != 1 or tenants[0]["code"] != "TN-XL-000317":
                raise SystemExit("租户结构与预期不符；未作修改")
            cursor.execute("SELECT id,code,name,account,dept,status,lock_version,updated_at FROM sys_user WHERE (code<>'U010001' AND status<>'disabled') OR (code='U010001' AND dept IS DISTINCT FROM %s) ORDER BY id", (NAME,))
            users = cursor.fetchall()
            # Never back up token hashes, passwords or other authentication material.
            cursor.execute("SELECT id,code,user_id,revoked_at,revoked_by,lock_version,updated_at FROM api_key WHERE principal_kind='user' AND user_id<>121 AND revoked_at IS NULL ORDER BY id")
            keys = cursor.fetchall()
            org_changes = [o for o in orgs if o["code"] != "hd" or (o["name"], o["seq"], o["is_default"]) != (NAME, 0, True)]
            tenant_changes = [t for t in tenants if (t["name"], t["short_name"], t["industry"]) != (NAME, NAME, "")]
            plan = {"sys_tenant": len(tenant_changes), "sys_org": len(org_changes), "sys_user": len(users), "api_key": len(keys)}
            print(json.dumps({"apply": apply, "changedRows": plan}, ensure_ascii=True))
            if not apply or not any(plan.values()):
                return
            before_anchor = fingerprint(cursor)
            backup_dir = ROOT.parent / "exports" / "database"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / ("single_account_org_before_" + datetime.now().strftime("%Y-%m-%d_%H%M%S_%f") + ".json")
            with backup.open("x", encoding="utf-8") as stream:
                json.dump({"database": url.database, "anchorSha256": before_anchor,
                           "sys_tenant": tenant_changes, "sys_org": org_changes,
                           "sys_user": users, "api_key": keys}, stream, ensure_ascii=False, indent=2, default=str)
            cursor.execute("UPDATE sys_tenant SET name=%s,short_name=%s,industry='',lock_version=lock_version+1,updated_at=now() WHERE id=1 AND (name IS DISTINCT FROM %s OR short_name IS DISTINCT FROM %s OR industry<>'')", (NAME, NAME, NAME, NAME))
            cursor.execute("UPDATE sys_org SET name=%s,seq=0,is_default=true,lock_version=lock_version+1,updated_at=now() WHERE id=1 AND (name IS DISTINCT FROM %s OR seq<>0 OR NOT is_default)", (NAME, NAME))
            cursor.execute("DELETE FROM sys_org WHERE code IN ('hn','hq') AND is_seed")
            cursor.execute("UPDATE sys_user SET status='disabled',lock_version=lock_version+1,updated_at=now() WHERE code<>'U010001' AND status<>'disabled'")
            cursor.execute("UPDATE sys_user SET dept=%s,lock_version=lock_version+1,updated_at=now() WHERE id=121 AND dept IS DISTINCT FROM %s", (NAME, NAME))
            cursor.execute("UPDATE api_key SET revoked_at=now(),revoked_by='admin',lock_version=lock_version+1,updated_at=now() WHERE principal_kind='user' AND user_id<>121 AND revoked_at IS NULL")
            # In-transaction assertions make an incomplete or damaging change roll back.
            assert fingerprint(cursor) == before_anchor, "历史身份、权限或本体成员锚点发生变化"
            cursor.execute("SELECT id FROM sys_user WHERE status='active'")
            assert cursor.fetchall() == [{"id": 121}]
            cursor.execute("SELECT code,name,is_default FROM sys_org ORDER BY id")
            assert cursor.fetchall() == [{"code": "hd", "name": NAME, "is_default": True}]
            cursor.execute("SELECT count(*) AS n FROM api_key WHERE principal_kind='user' AND user_id<>121 AND revoked_at IS NULL")
            assert cursor.fetchone()["n"] == 0
    print("Applied; nonsecret before-image: " + str(backup))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    run(parser.parse_args().apply)
