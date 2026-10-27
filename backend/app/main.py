from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api import include_routers
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import setup_logging
from app.core.request_id import RequestIdMiddleware
from app.db.session import dispose_engine, get_sessionmaker
from app.modules.data.secrets import check_secret_key
from app.modules.admin.api_key_calls import ApiKeyCallsMiddleware
from app.modules.qa.eval.job import recover_interrupted_runs
from app.modules.versioning.sections import load_sections


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_secret_key()  # 数据源密码密钥：prod / 真实连接没配就不启动（app/modules/data/secrets.py）
    try:
        await recover_interrupted_runs()
        yield
    finally:
        await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(s.log_level)
    app = FastAPI(title=s.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(ApiKeyCallsMiddleware)
    install_error_handlers(app)
    include_routers(app)
    load_sections()  # 唯一登记点：app/modules/<模块>/sections.py（幂等）

    @app.get("/healthz", tags=["系统"], summary="存活检查（不查库）")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", tags=["系统"], summary="就绪检查（查库）")
    async def readyz() -> dict:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}

    return app


app = create_app()
