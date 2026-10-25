"""迁移里用的 DDL 小工具（只追加表的数据库层兜底）。

用法（迁移里）：

    from app.db.ddl import append_only_guard_function, enable_append_only, disable_append_only
    op.execute(append_only_guard_function())        # 0001 里建一次，所有表共用
    for sql in enable_append_only("ontology_release"):
        op.execute(sql)

规则：禁止 DELETE、TRUNCATE 和真正改变内容的 UPDATE；内容完全相同的 UPDATE 放行
（seed 的 UPSERT 可以重复执行，保持幂等）。日志、版本、事件、命中记录这类只追加表都用它。
"""

GUARD_FUNCTION = "append_only_guard"


def append_only_guard_function() -> str:
    return f"""
    CREATE OR REPLACE FUNCTION {GUARD_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN
        RETURN NEW;
      END IF;
      RAISE EXCEPTION '% 只追加，不允许 %', TG_TABLE_NAME, TG_OP USING ERRCODE = 'raise_exception';
    END $$
    """


def enable_append_only(table: str) -> list[str]:
    return [
        f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}()",
        f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {GUARD_FUNCTION}()",
    ]


def disable_append_only(table: str) -> list[str]:
    return [
        f"DROP TRIGGER IF EXISTS trg_{table}_no_truncate ON {table}",
        f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}",
    ]
