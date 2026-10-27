"""dynamic:read 携带 rules 页 view：meta:read + dynamic:read 的密钥能看到能力目录的 rules 分节。

运行：pytest -p tests.use_ops_support tests/test_api_key_dynamic_capabilities.py
"""

from tests.test_palantir_machine import key
from tests.test_use_ops_runtime import API, alert_runtime, runtime  # noqa: F401

URL = f"{API}/meta/capabilities"


async def test_meta_read_plus_dynamic_read_sees_rules_section(alert_runtime, client):  # noqa: F811
    meta_only = {**(await key(client, ["meta:read"])), "X-Ontology-Id": "1"}
    body = (await client.get(URL, headers=meta_only)).json()
    assert "rules" not in body and "rules" in {x["section"] for x in body["omitted"]}

    both = {**(await key(client, ["meta:read", "dynamic:read"])), "X-Ontology-Id": "1"}
    res = await client.get(URL, headers=both)
    assert res.status_code == 200, res.text
    body = res.json()
    assert "rules" not in {x["section"] for x in body["omitted"]}
    assert "OPS-RULE" in {r["id"] for r in body["rules"]}

    # dynamic:read 单独不能到达只声明 meta:read 的路由。
    only_dynamic = {**(await key(client, ["dynamic:read"])), "X-Ontology-Id": "1"}
    res = await client.get(URL, headers=only_dynamic)
    assert res.status_code == 403 and "meta:read" in res.json()["details"]["required"]
