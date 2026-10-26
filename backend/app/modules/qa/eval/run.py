"""跑评测题：真实 LLM（key 只从环境读，不打印），判分规则见 docs/backend/qa-eval.md §5。

- answer：结果行跟 goldSql 结果按行多重集合比（数值保留 1 位）；
  答成澄清只在题目 checks 带 orClarify 时算对。
- clarify：answer 轮带 clarify 且没执行 SQL。
- refuse：denied 且 code == refuseCode；SQL_REJECTED 只在 refuseCode = NOT_SELECT 时算对。
- 每题一个只读事务，跑完回滚；用户 U010001（admin，asScope 题测不了）。
结果：seeds/extra/qa/eval/results/result_{sel}{TAG}.json（逐题明细）+ summary{TAG}.json（按类型统计）。
"""

import asyncio
import json
import os
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


async def one(eng, llm, q, sem):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.auth import resolve_user
    from app.core.errors import AppError
    from app.modules.qa import chain

    async with sem, eng.connect() as conn:
        tr = await conn.begin()
        await conn.execute(text("set local statement_timeout = 15000"))
        s = AsyncSession(bind=conn)
        user = await resolve_user(s, "U010001")
        exp_t = q["expectedType"]
        r = {"code": q["code"], "exp": exp_t, "q": q["question"]}
        t0 = time.monotonic()
        try:
            kind, snap, _n, _ms, sql = await chain.answer(s, conn, llm, user, q["question"])
            r["sql"], r["kind"] = sql, kind
            ans = snap.get("answer") or {}
            items = (ans.get("table") or {}).get("items") or []
            r["snap"] = {k: v for k, v in (ans or snap).items() if k != "table"}
            pred = [list(it.values()) for it in items]
            r["pred"] = pred[:5]
            if exp_t == "clarify":
                r["ok"] = kind == "answer" and "clarify" in ans and sql is None
            elif exp_t == "refuse":
                r["ok"] = kind == "denied" and snap.get("code") == q.get("refuseCode")
                r["anyRefuse"] = kind == "denied"
            elif "clarify" in ans:
                r["ok"] = "orClarify" in (q.get("checks") or [])
            elif exp_t == "answer" and q.get("goldSql"):
                async with conn.begin_nested():
                    rows = (await conn.execute(text(q["goldSql"]))).fetchall()
                g = chain._label_rows([[chain._cell(v) for v in row] for row in rows])
                r["gold"] = g[:5]
                gnull = all(all(x is None for x in row) for row in g)
                zero = pred and all(all(x in (None, 0) for x in row) for row in pred)
                r["ok"] = bool(match(g, pred) or (kind == "empty" and (not g or gnull)) or (gnull and zero))
            else:
                r["ok"] = False
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
    llm, sem = get_llm(), asyncio.Semaphore(6)
    res = await asyncio.gather(*(one(eng, llm, q, sem) for q in qs))
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
