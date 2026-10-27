"""导入草稿的取值提示：通用规则（低基数 → 维度、两值 → 是/否提示、空列 / 文本时间提示），大模型关闭。

零售风格的表名、列名只出现在本测试夹具里，实现里没有任何行业词表。
运行：pytest tests/test_ontology_import_value_hints.py（独立测试库真实 PG，只读源表）
"""

from sqlalchemy import text

from app.modules.auto_ontology.heuristic import build_proposal
from app.modules.auto_ontology.introspect import Column, Table
from app.modules.data.ontology_import import MAX_ENUM_VALUES, value_hints
from tests.test_datasource_ontology_import import ADMIN, P, imp, source  # noqa: F401
from tests.use_objects_support import bind_objects_workspace


def retail_table():
    cols = [
        Column("line_id", "bigint", "integer", False, None),
        Column("line_status", "text", "string", True, "行状态"),
        Column("is_test", "text", "string", True, None),
        Column("shipped_at", "text", "string", True, None),
        Column("memo", "text", "string", True, None),
        Column("sku_name", "text", "string", True, None),
    ]
    return Table("order_line", "订单行", cols, ["line_id"], [], [])


def test_value_hints_pure():
    t = retail_table()
    proposal = build_proposal([t], "源")
    samples = {
        "order_line": {
            "line_status": ["已发货", "待发货", "已取消"],
            "is_test": ["0", "1"],
            "shipped_at": ["2026-01-01"],
            "memo": [],
            "sku_name": [f"sku{i}" for i in range(MAX_ENUM_VALUES + 1)],
        }
    }
    hints = value_hints(proposal, [t], samples)
    props = {p["source"]: p for p in proposal["objectTypes"][0]["props"]}
    status = props["line_status"]
    assert status["dim"] is True and status["filterable"] is True
    assert status["desc"].startswith("行状态；取值样本：") and "共 3 个" in status["desc"]
    assert not props["sku_name"].get("dim")  # 高基数不当维度
    assert not props["is_test"].get("filterable")  # 两值只提示，不改
    assert any("is_test" in h and "是 / 否" in h for h in hints)
    assert any("shipped_at" in h and "时间" in h for h in hints)
    assert any("memo" in h and "没有非空取值" in h for h in hints)
    assert props["line_id"]["type"] == "integer" and "dim" not in props["line_id"]
    # 不采样时只有列名提示，不改属性
    p2 = build_proposal([t], "源")
    assert [h for h in value_hints(p2, [t], {}) if "shipped_at" in h]
    assert not any(p.get("dim") for p in p2["objectTypes"][0]["props"] if p["source"] == "line_status")


async def test_preview_sample_values_opt_in(imp, client, engine):  # noqa: F811
    session, code, schema = imp
    await bind_objects_workspace(session)  # 预览路由按本体工作空间成员校验
    await session.commit()
    async with engine.begin() as conn:
        await conn.execute(
            text(f'CREATE TABLE "{schema}".order_line (id bigint PRIMARY KEY, status text, is_test text)')
        )
        await conn.execute(
            text(
                f"INSERT INTO \"{schema}\".order_line VALUES (1,'paid','0'),(2,'shipped','1'),(3,'paid','0')"
            )
        )
    url = f"{P}/{code}/import-preview"
    plain = await client.post(url, json={"schema": schema, "tables": ["order_line"]}, headers=ADMIN)
    assert plain.status_code == 200, plain.text
    body = plain.json()
    assert body["llm"] is False and body["sampled"] is False and body["generator"] == "规则"
    data = {"schema": schema, "tables": ["order_line"], "sampleValues": True}
    res = await client.post(url, json=data, headers=ADMIN)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["sampled"] is True and out["fingerprint"] == body["fingerprint"]  # 指纹只覆盖结构
    props = {p["source"]: p for p in out["proposal"]["objectTypes"][0]["props"]}
    assert props["status"]["dim"] is True and "paid、shipped" in props["status"]["desc"]
    assert any("is_test" in w for w in out["warnings"])
