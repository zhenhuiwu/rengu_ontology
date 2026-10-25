"""打印评测库上的系统 prompt（调口径 / 上下文用，不含 key）。"""

import asyncio

from app.modules.qa.eval import setup_env


async def main() -> None:
    url = setup_env()
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    import app.main  # noqa: F401  注册全部模型
    from app.modules.qa import chain

    eng = create_async_engine(url)
    async with AsyncSession(eng) as s:
        ctx = await chain.build_context(s)
        print(chain.build_prompt("Q", ctx)[0]["content"])
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
