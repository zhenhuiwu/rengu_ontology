"""Compile business formulas against synthetic metadata, without opening a database."""

from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.common.sql_guard import assert_single_select, inject_row_filters
from app.modules.static import metric_trial as trial
from app.modules.static.metricflow_export import _export_metric
from scripts.check_metric_snapshot_export import fixture


def check():
    obj, gov = fixture()
    row = SimpleNamespace(
        code="case_count",
        api_name="case_count",
        display_name="合成条件指标",
        version="v1",
        unit="项",
        status=SimpleNamespace(value="已发布"),
    )
    with patch.object(trial, "governed_tables", return_value={gov.table.name: gov}):
        for formula, expected in (
            ("SUM(CASE WHEN Asset.amount > 10 THEN Asset.amount ELSE 0 END)", "CASE WHEN"),
            ("COUNT(DISTINCT Asset.id)", "count(DISTINCT"),
            ("ROUND(COALESCE(AVG(Asset.amount),0),2)", "round("),
            ("SUM(ABS(Asset.amount))", "abs("),
        ):
            definition = {"formula": formula, "def": []}
            _, expression, *_ = trial.compile_metric(definition, [obj], 1)
            sql = str(
                expression.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
            )
            assert expected in sql, sql
            exported = _export_metric(definition, row, [obj], 1, {})
            assert exported[0]["type"] == "program", "complex formulas must use the validated evaluator"
        for formula in (
            "SUM(SUM(Asset.amount))",
            "CASE WHEN Asset.amount > 10 THEN SUM(Asset.amount) ELSE 0 END",
            "SUM(ROUND(Asset.id,2))",
            "SUM(ROUND(Asset.created_at,2))",
            "SUM(ABS(Asset.id))",
            "SUM(Asset.id + 1)",
            "SUM(CASE WHEN Asset.amount > 10 THEN Asset.amount ELSE 'not-number' END)",
            "COUNT(CASE WHEN Asset.amount > 10 THEN Asset.amount ELSE Asset.created_at END)",
            "COUNT(COALESCE(Asset.amount,Asset.id))",
            "COUNT(COALESCE(ABS(Asset.amount),'x'))",
        ):
            try:
                trial.compile_metric({"formula": formula, "def": []}, [obj], 1)
            except trial.AppError:
                pass
            else:
                raise AssertionError(formula)
        definition = {
            "formula": ("SUM(CASE WHEN Asset.amount > 10 THEN Asset.amount ELSE 0 END) "
                        "WHERE Asset.amount < 100"),
            "def": [{"k": "默认过滤", "v": "Asset.amount > 1"}],
        }
        _, expression, _, _, conditions = trial.compile_metric(definition, [obj], 1)
        sql = str(select(expression).select_from(gov.table).where(*conditions).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
        ))
        assert_single_select(sql)
        guarded, _ = inject_row_filters(sql, {gov.table.name: ("TRUE", [])},
                                        projections={gov.table.name: gov.projection})
        assert "CASE WHEN" in guarded and "amount < 100" in guarded and "amount > 1" in guarded
    print("PASS: conditional aggregates, distinct counts, null-aware rounding and unsafe nesting rejection")


if __name__ == "__main__":
    check()
