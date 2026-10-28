"""跑评测题：真实 LLM（key 只从环境读，不打印），判分规则见 docs/backend/qa-eval.md §5。

- answer：结果行跟 goldSql 结果按行多重集合比（数值保留 1 位）；
  答成澄清只在题目 checks 带 orClarify 时算对。
- clarify：answer 轮带 clarify 且没执行 SQL。
- refuse：denied 且 code == refuseCode；SQL_REJECTED 只在 refuseCode = NOT_SELECT 时算对。
- 每题一个只读事务，跑完回滚；用户 = QA_EVAL_USER，
  未设时取「无行级范围限制的角色」里编号最小的成员（seed 里的管理员）；asScope 题把 asScope 当本轮会话范围传入
  （p1 起；之前的基线没传，这几题与 76/100 基线不完全可比）。
结果：seeds/extra/qa/eval/results/result_{sel}{TAG}.json（逐题明细）+ summary{TAG}.json（按类型统计）。
"""

import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from app.modules.qa.eval import setup_env

EVAL_DIR = Path("seeds/extra/qa/eval")


def norm(v):
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return f"{round(float(v), 1):g}"
    return str(v)


def match(gold, pred) -> bool:
    if len(gold) != len(pred):
        return False
    pc = [Counter(norm(x) for x in r) for r in pred]
    used = set()
    for g in gold:
        gc = Counter(norm(x) for x in g)
        for i, p in enumerate(pc):
            if i not in used and not (gc - p):
                used.add(i)
                break
        else:
            return False
    return True


def has_draft_notice(kind, snap, sql):
    """Accept a visible affirmative notice, never a mention in SQL, the question or a title.

    Canonical independent clauses avoid scoring '不是草稿口径' as an affirmative notice.
    This checks disclosure only; the existing result comparison must also pass.
    """
    if kind != "answer" or not isinstance(sql, str) or not sql.strip():
        return False
    ans = snap.get("answer") or {}
    if ans.get("card") == "clarify":
        return False
    texts = (ans.get("text"), (ans.get("basis") or {}).get("caliber"))
    # Fail closed on explicit conflicting published-caliber statements anywhere visible.
    if any(isinstance(value, str) and "已发布口径" in value for value in texts):
        return False
    return any(
        clause.strip() in {"草稿口径", "本结果采用草稿口径"}
        for value in texts
        if isinstance(value, str)
        and not re.search(
            r"(?:不是|并非|未采用|不采用|没有采用|无需|无须|非)\s*[，,；;：:]?\s*(?:本结果采用)?草稿口径",
            value,
        )
        for clause in re.split(r"[。；;，,\n]", value)
    )


async def eval_user_code(s) -> str:
    """评测身份：QA_EVAL_USER，或无 sys_role_scope 限制的角色里编号最小的成员（全量数据视角）。"""
    if os.environ.get("QA_EVAL_USER"):
        return os.environ["QA_EVAL_USER"]
    from sqlalchemy import text

    code = await s.scalar(
        text("""
        SELECT u.code FROM sys_user u JOIN sys_role_member m ON m.user_id = u.id
        JOIN sys_role r ON r.id = m.role_id
        WHERE u.status = 'active'
          AND NOT EXISTS (SELECT 1 FROM sys_role_scope sc WHERE sc.role_id = r.id)
        ORDER BY r.id, u.code LIMIT 1
    """)
    )
    if code is None:
        raise RuntimeError("评测库里没有不受行级范围限制的用户，请设置 QA_EVAL_USER")
    return code


async def one(eng, llm, q, sem):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.auth import resolve_user
    from app.common.ontology import DEFAULT_ONTOLOGY_ID, set_ontology_scope
    from app.core.errors import AppError
    from app.modules.qa import chain

    async with sem, eng.connect() as conn:
        tr = await conn.begin()
        await conn.execute(text("set local statement_timeout = 15000"))
        s = AsyncSession(bind=conn)
        await set_ontology_scope(s, DEFAULT_ONTOLOGY_ID)
        user = await resolve_user(s, await eval_user_code(s))
        exp_t = q["expectedType"]
        r = {"code": q["code"], "exp": exp_t, "q": q["question"]}
        t0 = time.monotonic()
        try:
            kind, snap, _n, _ms, sql = await chain.answer(
                s, conn, llm, user, q["question"], session_scope=q.get("asScope")
            )
            r["sql"], r["kind"] = sql, kind
            ans = snap.get("answer") or {}
            items = (ans.get("table") or {}).get("items") or []
            r["snap"] = {k: v for k, v in (ans or snap).items() if k != "table"}
            pred = [list(it.values()) for it in items]
            r["pred"] = pred[:5]
            no_payload = not any(k in snap or k in ans for k in ("table", "action"))
            if exp_t == "clarify":
                r["ok"] = kind == "answer" and "clarify" in ans and sql is None and no_payload
            elif exp_t == "refuse":
                r["ok"] = (
                    kind == "denied"
                    and snap.get("code") == q.get("refuseCode")
                    and sql is None
                    and "answer" not in snap
                    and no_payload
                )
                r["anyRefuse"] = kind == "denied"
            elif "clarify" in ans:
                r["ok"] = (
                    kind == "answer" and sql is None and no_payload and "orClarify" in (q.get("checks") or [])
                )
            elif exp_t == "answer" and q.get("goldSql"):
                async with conn.begin_nested():
                    rows = (await conn.execute(text(q["goldSql"]))).fetchall()
                g = chain._label_rows([[chain._cell(v) for v in row] for row in rows], DEFAULT_ONTOLOGY_ID, s)
                r["gold"] = g[:5]
                gnull = bool(g) and all(all(x is None for x in row) for row in g)
                zero = (
                    bool(pred)
                    and len(pred) == len(g)
                    and all(all(x in (None, 0) for x in row) for row in pred)
                )
                table_answer = (
                    kind == "answer"
                    and isinstance(ans.get("table"), dict)
                    and "action" not in ans
                    and "clarify" not in ans
                    and "action" not in snap
                    and "table" not in snap
                )
                empty_answer = kind == "empty" and "answer" not in snap and no_payload
                r["ok"] = bool(
                    (table_answer and (match(g, pred) or (gnull and zero)))
                    or (empty_answer and (not g or gnull))
                )
            else:
                r["ok"] = False
            if r["ok"] and "draftNotice" in (q.get("checks") or []):
                r["ok"] = has_draft_notice(kind, snap, sql)
                if not r["ok"]:
                    r["checkFailure"] = "缺少明确的草稿口径提示"
        except AppError as e:
            r["err"] = e.code
            r["ok"] = exp_t == "refuse" and e.code == "SQL_REJECTED" and q.get("refuseCode") == "NOT_SELECT"
        except Exception as e:  # 评测要跑完全部题，单题异常记下来
            r["err"] = f"{type(e).__name__}: {str(e)[:200]}"
            r["ok"] = False
        r["s"] = round(time.monotonic() - t0, 1)
        await tr.rollback()
        print("OK  " if r["ok"] else "FAIL", r["code"], exp_t, r.get("err", ""), flush=True)
        return r


async def main(sel: str) -> None:
    url = setup_env()
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.main  # noqa: F401  注册全部模型
    from app.modules.qa.llm import get_llm

    qs = json.loads((EVAL_DIR / "questions.json").read_text(encoding="utf-8"))
    if sel != "all":
        a, b = map(int, sel.split("-"))
        qs = [q for q in qs if a <= int(q["code"][-3:]) <= b]
    eng = create_async_engine(url, pool_size=8)
    from app.db.session import get_sessionmaker
    from app.modules.admin.model_settings import load_model_runtime

    async with get_sessionmaker()() as settings_session:
        llm = get_llm(await load_model_runtime(settings_session, "qa"))
    sem = asyncio.Semaphore(6)
    res = await asyncio.gather(*(one(eng, llm, question, sem) for question in qs))
    await eng.dispose()
    tag = os.environ.get("TAG", "")
    out = EVAL_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"result_{sel}{tag}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    by: dict[str, dict[str, int]] = {}
    for r in res:
        d = by.setdefault(r["exp"], {"ok": 0, "total": 0})
        d["total"] += 1
        d["ok"] += int(r["ok"])
    summary = {
        "sel": sel,
        "tag": tag,
        "model": llm.model,
        "fixedNow": os.environ.get("RENGU_FIXED_NOW"),
        "ok": sum(r["ok"] for r in res),
        "total": len(res),
        "byType": by,
        "failed": sorted(r["code"] for r in res if not r["ok"]),
    }
    (out / f"summary{tag}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("TOTAL", summary["ok"], "/", summary["total"], by)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "all"))
