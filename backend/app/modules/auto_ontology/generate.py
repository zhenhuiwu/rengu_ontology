"""把内省 → 规则 → 大模型润色串起来；纯函数式，不写库（调用方决定怎么用结果）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.auto_ontology import heuristic
from app.modules.auto_ontology.introspect import introspect
from app.modules.auto_ontology.llm import LLMClient, LlmError, build_messages, merge, parse_reply

NO_KEY_WARNING = "没有配置大模型（DASHSCOPE_API_KEY），名称和说明按规则生成"


class EmptySchema(AppError):
    status_code, code, message = 409, "EMPTY_INPUT", "没有可生成的表"


@dataclass
class Generated:
    tables: list[str]
    proposal: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    generator: str = "规则"  # 规则 / 大模型
    model: str | None = None


async def enrich(proposal: dict[str, Any], llm: LLMClient | None, warnings: list[str]) -> str | None:
    """就地用大模型润色 proposal；返回用到的模型名，没用上返回 None（原因记进 warnings）。"""
    if llm is None:
        warnings.append(NO_KEY_WARNING)
        return None
    try:
        changed = merge(proposal, parse_reply(await llm.complete(build_messages(proposal))))
    except LlmError as e:
        warnings.append(f"{e}，已退回规则生成的名称和说明")
        return None
    if changed == 0:
        warnings.append("大模型没有给出可用的修改，结果与规则生成相同")
    return llm.model


async def generate(
    session: AsyncSession,
    schema: str,
    tables: list[str] | None = None,
    *,
    datasource_name: str = "本应用库（PostgreSQL）",
    use_llm: bool = True,
    llm: LLMClient | None = None,
) -> Generated:
    found = await introspect(session, schema, tables)
    if not found:
        raise EmptySchema(details={"schema": schema})
    proposal = heuristic.build_proposal(found, datasource_name)
    out = Generated([t.name for t in found], proposal, heuristic.structural_warnings(found))
    if use_llm:
        model = await enrich(proposal, llm, out.warnings)
        if model is not None:
            out.generator, out.model = "大模型", model
    return out
