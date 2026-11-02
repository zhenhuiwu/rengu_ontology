"""Read-only seed inventory in one explicit workspace; never deletes or exports payloads.

Run from backend: python scripts/audit_seed_events.py --ontology 1 --database NAME
The database name must match RENGU_DATABASE_URL. Connection secrets are never printed.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def seed_manifest():
    root = Path(__file__).resolve().parents[1] / "seeds" / "json" / "action"
    definitions = json.loads((root / "EVENT_TYPES.json").read_text(encoding="utf-8"))
    calls = json.loads((root / "derived" / "eventsOf.json").read_text(encoding="utf-8"))
    events = [row for call in calls for row in call["result"]]
    return {r["id"] for r in definitions}, {r["id"] for r in events}


def audit(ontology_id: int, database: str) -> dict:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings

    if ontology_id <= 0:
        raise ValueError("工作空间编号必须为正整数")
    url = make_url(get_settings().database_url)
    if not url.drivername.startswith("postgresql") or url.database != database:
        raise ValueError("目标数据库与配置不一致；未连接数据库")
    codes, event_codes = seed_manifest()
    with (
        psycopg.connect(
            host=url.host,
            port=url.port or 5432,
            dbname=url.database,
            user=url.username,
            password=url.password,
            connect_timeout=8,
            row_factory=dict_row,
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '15s'")
        cursor.execute("SET LOCAL ROLE rengu_ontology_scoped")
        cursor.execute("SELECT set_config('rengu.ontology_id', %s, true)", (str(ontology_id),))
        cursor.execute("SELECT id FROM ontology_workspace WHERE id=%s AND deleted_at IS NULL", (ontology_id,))
        if cursor.fetchone() is None:
            raise ValueError("工作空间不存在或已删除")
        cursor.execute(
            """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname=ANY(%s)
                ORDER BY c.relname
            """,
            (["event", "event_type", "event_payload_field", "event_daily_stat"],),
        )
        policies = cursor.fetchall()
        if len(policies) != 4 or any(
            not r["relrowsecurity"] or not r["relforcerowsecurity"] for r in policies
        ):
            raise ValueError("事件表 RLS 未完整启用；停止审计，先核对工作空间迁移")
        cursor.execute(
            """
                SELECT t.id, t.code, t.is_seed,
                    count(e.id) AS event_count,
                    count(e.id) FILTER (WHERE e.is_seed) AS marked_seed_events,
                    count(e.id) FILTER (WHERE NOT e.is_seed) AS nonseed_events,
                    count(e.id) FILTER (WHERE e.is_test) AS test_events,
                    count(e.id) FILTER (WHERE e.is_seed AND e.code=ANY(%s)) AS marked_seed_ids_in_manifest,
                    count(e.id) FILTER (WHERE e.ontology_id<>%s) AS unexpected_workspace_rows
                FROM event_type t LEFT JOIN event e ON e.event_type_id=t.id
                WHERE t.ontology_id=%s AND t.code=ANY(%s)
                GROUP BY t.id,t.code,t.is_seed ORDER BY t.code
            """,
            (sorted(event_codes), ontology_id, ontology_id, sorted(codes)),
        )
        inventory = cursor.fetchall()
        parent_ids = {"event_type": [r["id"] for r in inventory]}
        cursor.execute(
            "SELECT id FROM event WHERE ontology_id=%s AND event_type_id=ANY(%s)",
            (ontology_id, parent_ids["event_type"]),
        )
        parent_ids["event"] = [r["id"] for r in cursor.fetchall()]
        cursor.execute("""
                SELECT p.relname AS parent_table, n.nspname AS child_schema,
                       c.relname AS child_table, a.attname AS child_column,
                       EXISTS (SELECT 1 FROM pg_attribute s WHERE s.attrelid=c.oid
                               AND s.attname='is_seed' AND NOT s.attisdropped) AS has_seed
                FROM pg_constraint f JOIN pg_class p ON p.oid=f.confrelid
                JOIN pg_namespace pn ON pn.oid=p.relnamespace
                JOIN pg_class c ON c.oid=f.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=f.conkey[1]
                WHERE f.contype='f' AND pn.nspname='public' AND p.relname IN ('event','event_type')
                  AND cardinality(f.conkey)=1 ORDER BY p.relname,c.relname,a.attname
            """)
        references = []
        for fk in cursor.fetchall():
            nonseed = (
                sql.SQL("count(*) FILTER (WHERE NOT is_seed)") if fk["has_seed"] else sql.SQL("NULL::bigint")
            )
            cursor.execute(
                sql.SQL("SELECT count(*) AS rows, {} AS nonseed_rows FROM {}.{} WHERE {}=ANY(%s)").format(
                    nonseed,
                    sql.Identifier(fk["child_schema"]),
                    sql.Identifier(fk["child_table"]),
                    sql.Identifier(fk["child_column"]),
                ),
                (parent_ids[fk["parent_table"]],),
            )
            references.append({**fk, **cursor.fetchone()})
        cursor.execute("SHOW transaction_read_only")
        assert cursor.fetchone()["transaction_read_only"] == "on"
        connection.rollback()
    return {
        "mode": "read-only",
        "ontologyId": ontology_id,
        "rls": policies,
        "manifestNameMatches": inventory,
        "foreignKeyReferences": references,
        "deletableRows": None,
        "reviewRequired": "名字或is_seed均不足以删除；还需比对完整种子内容、改动历史、软引用和版本快照。",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ontology", required=True, type=int)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(audit(args.ontology, args.database), ensure_ascii=False, indent=2))
    except Exception:
        # Driver errors may contain DSNs or SQL parameters. Preserve no such details in stdout/stderr.
        raise SystemExit("审计未完成；未改动记录。请核对目标、权限、迁移与连接（不输出连接凭据）。") from None
