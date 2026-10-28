"""自动出题的过拟合抽查：按本体元数据现拼题（`eval_gen`，不写库），抽样后真模型作答，按 `job.judge` 判分。

用法（backend/ 下，key 只从环境读）：`TAG=_p5 N=20 .venv/bin/python -m app.modules.qa.eval.auto`
抽样：指标题、动作题各按固定步长取（确定性，可复现），合计至多 N 题。
结果：seeds/extra/qa/eval/results/result_auto{TAG}.json + summary_auto{TAG}.json。
"""

import asyncio
import json
import os
import time
from types import SimpleNamespace

from app.modules.qa.eval import setup_env
from app.modules.qa.eval.run import EVAL_DIR, eval_user_code


def sample(items: list, n: int) -> list:
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


async def main() -> None:
    url = setup_env()
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    import app.main  # noqa: F401  注册全部模型
    from app.common.auth import resolve_user
    from app.common.ontology import DEFAULT_ONTOLOGY_ID, set_ontology_scope
    from app.db.session import get_sessionmaker
    from app.modules.admin.model_settings import load_model_runtime
    from app.modules.qa import catalog, chain, eval_gen
    from app.modules.qa.eval.job import judge
    from app.modules.qa.llm import get_llm

    n = int(os.environ.get("N", "20"))
    async with get_sessionmaker()() as s0:
        llm = get_llm(await load_model_runtime(s0, "qa"))
    eng = create_async_engine(url, pool_size=8)
    async with eng.connect() as conn:
        s = AsyncSession(bind=conn)
        await set_ontology_scope(s, DEFAULT_ONTOLOGY_ID)
        user = await resolve_user(s, await eval_user_code(s))
        mq = eval_gen.metric_questions(await eval_gen._metrics(s))
        aq = eval_gen.action_questions(await eval_gen._actions(s, user))
        await conn.rollback()
    na = min(len(aq), max(n // 4, 1)) if aq else 0
    picked = sample(mq, n - na) + sample(aq, na)
    sem = asyncio.Semaphore(6)

    async def one(item):
        question, expect, src = item
        r = {"q": question, "expect": expect, "source": src}
        async with sem, eng.connect() as conn:
            await conn.begin()
            await conn.execute(text("set local statement_timeout = 15000"))
            s = AsyncSession(bind=conn)
            await set_ontology_scope(s, DEFAULT_ONTOLOGY_ID)
            u = await resolve_user(s, await eval_user_code(s))
            metrics, _ = await catalog.directory(s)
            t0 = time.monotonic()
            try:
                kind, snap, _n, _ms, sql = await chain.answer(s, conn, llm, u, question)
                ans = snap.get("answer") or {}
                r.update(kind=kind, sql=sql, card=ans.get("card"), text=(ans.get("text") or "")[:300])
                res = judge(SimpleNamespace(expect=expect), kind, snap, metrics, sql=sql)
                r["ok"], r["why"] = res is None, (res[1] if res else "")
            except Exception as e:  # 抽查要跑完，单题异常记下
                r["ok"], r["err"] = False, f"{type(e).__name__}: {str(e)[:200]}"
            r["s"] = round(time.monotonic() - t0, 1)
            await conn.rollback()
        print("OK  " if r["ok"] else "FAIL", src, question, r.get("why") or r.get("err", ""), flush=True)
        return r

    res = await asyncio.gather(*(one(item) for item in picked))
    await eng.dispose()
    tag = os.environ.get("TAG", "")
    out = EVAL_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"result_auto{tag}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), "utf-8"
    )
    by: dict[str, dict[str, int]] = {}
    for r in res:
        d = by.setdefault(r["source"].split(":")[0], {"ok": 0, "total": 0})
        d["total"] += 1
        d["ok"] += int(r["ok"])
    summary = {
        "tag": tag,
        "model": llm.model,
        "pool": {"metric": len(mq), "action": len(aq)},
        "ok": sum(r["ok"] for r in res),
        "total": len(res),
        "bySource": by,
        "failed": [r["q"] for r in res if not r["ok"]],
    }
    (out / f"summary_auto{tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    print("TOTAL", summary["ok"], "/", summary["total"], by, summary["pool"])


if __name__ == "__main__":
    asyncio.run(main())
