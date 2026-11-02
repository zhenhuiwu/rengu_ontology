"""Rename the existing seeded default identity without changing its ID or permissions."""

import json
from datetime import datetime
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
raw = dotenv_values(root / ".env").get("RENGU_DATABASE_URL")
if not raw:
    raise SystemExit("backend/.env 中没有配置平台数据库")
url = make_url(raw)
if url.database != "rengu_ontology" or not url.drivername.startswith("postgresql"):
    raise SystemExit("仅支持配置名为 rengu_ontology 的 PostgreSQL 平台库；未作修改")

with psycopg.connect(host=url.host, port=url.port or 5432, dbname=url.database,
                     user=url.username, password=url.password, connect_timeout=8) as connection:
    with connection.cursor() as cursor:
        cursor.execute("LOCK TABLE sys_user IN SHARE ROW EXCLUSIVE MODE")
        cursor.execute("SELECT id, code, name, account, is_seed, lock_version FROM sys_user WHERE code=%s FOR UPDATE", ("U010001",))
        row = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM sys_user WHERE lower(account)='admin' AND code<>%s", ("U010001",))
        conflict = cursor.fetchone()[0]
        if not row or not row[4] or conflict or row[2] not in ("王敏", "admin") or row[3] not in (None, "admin"):
            raise SystemExit("默认账号已被修改、不是预置账号或存在 admin 冲突；未作修改")
        if row[2] == row[3] == "admin":
            print("默认账号已经是 admin，无需修改。")
            raise SystemExit(0)
        backup_dir = root.parent / "exports" / "database"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / ("default_identity_before_" + datetime.now().strftime("%Y-%m-%d_%H%M%S_%f") + ".json")
        with backup.open("x", encoding="utf-8") as stream:
            json.dump({"id": row[0], "code": row[1], "name": row[2], "account": row[3], "lockVersion": row[5]}, stream, ensure_ascii=False)
        cursor.execute("UPDATE sys_user SET name=%s, account=%s, lock_version=lock_version+1, updated_at=now() WHERE id=%s AND lock_version=%s",
                       ("admin", "admin", row[0], row[5]))
        if cursor.rowcount != 1:
            raise RuntimeError("账号被并发修改，事务已回滚")
print("默认账号已更新；用户编号、角色与数据归属保持不变。")
