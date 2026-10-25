"""大模型润色：qwen-flash（百炼 DashScope OpenAI 兼容接口），写法同 app/modules/qa/llm.py。

- key 只读环境变量 DASHSCOPE_API_KEY（不打印、不入库）；模型 AUTO_ONTOLOGY_LLM_MODEL（默认 qwen-flash），
  AUTO_ONTOLOGY_LLM_BASE_URL、AUTO_ONTOLOGY_LLM_TIMEOUT_S 也走环境变量。
- 没配 key：get_llm() 返回 None，生成走规则结果（不报 503：规则结果本身就是可审核的草稿）。
- 大模型只能改「显示名 / 分组 / 说明 / 反向名」；返回里的未知 id、结构字段一律忽略
  （主键、类型、外键、基数以内省为准，同 NVIDIA auto-ontology 的「结构确定、描述交给模型」）。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol

import httpx

DEFAULT_MODEL = "qwen-flash"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class LlmError(Exception):
    """调用失败 / 返回无法解析。服务层捕获后退回规则结果，并把 str(e) 记进 warnings（不含 key）。"""


class LLMClient(Protocol):
    model: str

    async def complete(self, messages: list[dict[str, str]]) -> str: ...


class DashScopeLLM:
    def __init__(self, api_key: str, model: str, base_url: str, timeout_s: float) -> None:
        self._key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def __repr__(self) -> str:  # 不把 key 带进日志 / traceback
        return f"DashScopeLLM(model={self.model!r})"

    async def complete(self, messages: list[dict[str, str]]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._key}"},
                )
        except httpx.HTTPError as e:
            raise LlmError(f"调用大模型失败：{type(e).__name__}") from None
        if r.status_code != 200:
            raise LlmError(f"大模型返回 {r.status_code}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise LlmError("大模型返回格式无法解析") from None


class FakeLLM:
    """测试用：固定返回文本（或抛 LlmError），记下收到的 messages。"""

    def __init__(self, reply: str | Exception, model: str = "fake-llm") -> None:
        self.reply = reply
        self.model = model
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def get_llm() -> LLMClient | None:
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        return None
    return DashScopeLLM(
        key,
        os.environ.get("AUTO_ONTOLOGY_LLM_MODEL") or DEFAULT_MODEL,
        os.environ.get("AUTO_ONTOLOGY_LLM_BASE_URL") or DEFAULT_BASE_URL,
        float(os.environ.get("AUTO_ONTOLOGY_LLM_TIMEOUT_S") or 60),
    )


SYSTEM_PROMPT = """\
你是企业本体建模助手。给你一个业务库的表结构和已经按规则生成的本体草稿（对象类型、属性、关系），
请只为它们起更好的中文业务名称和一句话说明，
并给对象类型归一个业务分组（如 交易 / 客户 / 组织 / 商品 / 供应链 / 系统）。

规则：
- 只依据表名、列名、类型、注释和外键推断，不要编造不存在的业务含义；不确定就保留原名。
- 不得新增或删除对象、属性、关系；不得修改主键、类型、外键、基数。
- 名称不超过 12 个汉字；说明一句话。
- 只输出 JSON，格式：
{"objectTypes":[{"id":"<对象id>","name":"…","group":"…","desc":"…",
  "props":[{"api":"<属性api>","name":"…","group":"…","desc":"…"}]}],
 "linkTypes":[{"id":"<关系id>","name":"…","reverse":"…","desc":"…"}]}"""


def build_messages(proposal: dict[str, Any]) -> list[dict[str, str]]:
    compact = {
        "objectTypes": [
            {
                "id": o["id"],
                "table": o["table"],
                "name": o["name"],
                "comment": o["desc"],
                "props": [
                    {"api": p["api"], "type": p["type"], "pk": p.get("pk", False), "comment": p["desc"]}
                    for p in o["props"]
                ],
            }
            for o in proposal["objectTypes"]
        ],
        "linkTypes": [
            {"id": lk["id"], "from": lk["from"], "to": lk["to"], "card": lk["card"], "key": lk["key"]}
            for lk in proposal["linkTypes"]
        ],
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
    ]


def parse_reply(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LlmError("大模型没有返回 JSON")
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise LlmError("大模型返回的 JSON 无法解析") from None
    if not isinstance(data, dict):
        raise LlmError("大模型返回的 JSON 不是对象")
    return data


def _text(v: Any, limit: int) -> str | None:
    return v.strip()[:limit] if isinstance(v, str) and v.strip() else None


def merge(proposal: dict[str, Any], reply: dict[str, Any]) -> int:
    """把大模型给的名字 / 分组 / 说明合并进 proposal（就地修改），返回改动的字段数。只认已有 id。"""
    changed = 0
    objs = {o["id"]: o for o in proposal["objectTypes"]}
    for ro in reply.get("objectTypes") or []:
        if not isinstance(ro, dict) or ro.get("id") not in objs:
            continue
        o = objs[ro["id"]]
        for k, lim in (("name", 64), ("group", 32), ("desc", 500)):
            if (v := _text(ro.get(k), lim)) and v != o[k]:
                o[k], changed = v, changed + 1
        props = {p["api"]: p for p in o["props"]}
        for rp in ro.get("props") or []:
            if not isinstance(rp, dict) or rp.get("api") not in props:
                continue
            p = props[rp["api"]]
            for k, lim in (("name", 64), ("group", 32), ("desc", 500)):
                v = _text(rp.get(k), lim)
                if k == "desc" and v and p["desc"].startswith("外键 → ") and "外键" not in v:
                    v = f"{p['desc'].split('；')[0]}；{v}"  # 外键说明保留（前端按「外键 →」识别）
                if v and v != p[k]:
                    p[k], changed = v, changed + 1
    links = {lk["id"]: lk for lk in proposal["linkTypes"]}
    for rl in reply.get("linkTypes") or []:
        if not isinstance(rl, dict) or rl.get("id") not in links:
            continue
        lk = links[rl["id"]]
        for k, lim in (("name", 64), ("reverse", 64), ("desc", 500)):
            if (v := _text(rl.get(k), lim)) and v != lk[k]:
                lk[k], changed = v, changed + 1
    return changed
