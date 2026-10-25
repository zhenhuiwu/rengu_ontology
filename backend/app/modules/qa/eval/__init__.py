"""qa 评测工具（真实 LLM 跑 seeds/extra/qa/eval/questions.json）。

不进 pytest（tests/ 之外、文件名不是 test_*）；不在应用里 import。用法（在 backend/ 下）：

    set -a; . <装 DASHSCOPE_API_KEY 的 env 文件>; set +a
    .venv/bin/python -m app.modules.qa.eval.setup_db          # 重建评测库并灌全部种子
    TAG=_r1 .venv/bin/python -m app.modules.qa.eval.run all    # 或 1-20；结果写 results/
    .venv/bin/python -m app.modules.qa.eval.ctx                # 打印系统 prompt（不含 key）

key 只从环境读（app.modules.qa.llm.get_llm），本工具不打印、不落盘。
库：QA_EVAL_DATABASE_URL（默认本机 rengu_qa_eval），业务时钟 RENGU_FIXED_NOW 默认 2026-10-25T15:42:00+08:00。
"""

import os

DEFAULT_URL = "postgresql+asyncpg://rengu:rengu@127.0.0.1:5440/rengu_qa_eval"


def setup_env() -> str:
    """先设环境再 import app（Settings 有缓存）。返回评测库 URL。"""
    url = os.environ.get("QA_EVAL_DATABASE_URL", DEFAULT_URL)
    os.environ["RENGU_DATABASE_URL"] = url
    os.environ["RENGU_ENV"] = "test"
    os.environ.setdefault("RENGU_FIXED_NOW", "2026-10-25T15:42:00+08:00")
    return url
