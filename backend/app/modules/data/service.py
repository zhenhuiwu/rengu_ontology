"""数据连接业务逻辑（api-index §3.1）。不 commit（SessionDep 一个请求一个事务）。

- 密码只写：Fernet 加密存（secrets.py，密钥 RENGU_DS_SECRET_KEY），任何接口都不返回；
- 测试连接：开了真实连接（connector.real_connect_enabled）且类型有驱动时真连，成功后顺带内省刷新库表清单；
  没有开关或驱动时明确拒绝，不回退演示测试结果。默认 10 秒超时；
- 删除：已发布定义或未发布变更单映射引用时 409；无引用的同步记录、库表清单级联删除。
"""

from typing import Any

from app.common.pagination import PageParams
from app.core import clock
from app.core.errors import AppError
from app.core.schema import DATETIME_FORMAT
from app.modules.data import connector
from app.modules.data.adapters import ConnectionTestResult
from app.modules.data.models import Datasource, DatasourceTable, DsStatus, DsType
from app.modules.data.repo import DatasourceRepo, DatasourceTableRepo
from app.modules.data.schemas import (
    DatasourceCreate,
    DatasourceOut,
    DatasourceUpdate,
    DsMeta,
    DsRun,
    DsSync,
    DsTableOut,
    DsTest,
    DsTestIn,
)
from app.modules.data.secrets import decrypt_password, encrypt_password

# 常量与 mock datasources.ts 一致（tests 校验和 seeds/json/system/*.json 相同）
DS_TYPES = [t.value for t in DsType]
DS_DEFAULT_PORT: dict[str, int] = {
    DsType.postgresql.value: 5432,
    DsType.dm8.value: 5236,
    DsType.kingbase.value: 54321,
    DsType.mysql.value: 3306,
    DsType.erp.value: 443,
}
DM_COMPAT = ["Oracle", "MySQL", "PostgreSQL", "SQL Server", "不兼容"]
KB_MODES = ["oracle", "pg", "mysql"]
ERP_SYSTEMS = ["用友 NC Cloud", "金蝶云·星空", "SAP S/4HANA"]
META = DsMeta(
    types=DS_TYPES,
    default_port=DS_DEFAULT_PORT,
    dm_compat=DM_COMPAT,
    kb_modes=KB_MODES,
    erp_systems=ERP_SYSTEMS,
)


class PasswordRequired(AppError):
    status_code, code, message = 422, "DS_PASSWORD_REQUIRED", "改了主机、端口或用户名，请重新填写密码"


class RealConnectUnavailable(AppError):
    status_code, code, message = 422, "REAL_CONNECT_UNAVAILABLE", "未开启真实连接，或该类型暂无驱动"


class IntrospectFailed(AppError):
    status_code, code, message = 502, "INTROSPECT_FAILED", "连接源库失败"


def _use_real(kind: DsType) -> bool:
    return connector.real_connect_enabled() and connector.supports(kind.value)


def _require_real(kind: DsType) -> None:
    if not connector.real_connect_enabled():
        raise RealConnectUnavailable("未开启真实连接，未执行测试", details={"reason": "disabled"})
    if not connector.supports(kind.value):
        raise RealConnectUnavailable(
            f"暂不支持真实连接 {kind.value}（缺驱动），未执行测试",
            details={"reason": "unsupported", "type": kind.value},
        )


def to_out(d: Datasource, runs=None) -> DatasourceOut:
    return DatasourceOut(
        id=d.code,
        name=d.name,
        type=d.type,
        host=d.host,
        port=d.port,
        database=d.database,
        user=d.username,
        compat_mode=d.compat_mode,
        database_mode=d.database_mode,
        erp_system=d.erp_system,
        app_id=d.app_id,
        ssl=d.ssl,
        status=d.status,
        last_test=DsTest.model_validate(d.last_test) if d.last_test else None,
        owner=d.owner,
        object_types=list(d.object_types or []),
        sync=DsSync(
            mode=d.sync_mode,
            cron=d.sync_cron,
            cron_text=d.sync_cron_text,
            window=d.sync_window,
            cursor_field=d.sync_cursor_field,
            next_run=d.sync_next_run,
        ),
        runs=[
            DsRun(time=r.run_at, result=r.result, rows=r.rows, secs=r.secs, msg=r.msg)
            for r in sorted(d.runs if runs is None else runs, key=lambda x: x.seq)
        ],
        table_count=d.table_count,
        created_at=d.created_on,
        note=d.note,
        ref_only=d.ref_only,
    )


def table_out(t: DatasourceTable) -> DsTableOut:
    return DsTableOut(
        schema=t.schema_name,
        name=t.name,
        comment=t.comment,
        rows=t.row_count,
        cols=t.col_count,
        object_type=t.object_type,
        updated_at=t.meta_updated_at,
        ref=t.ref,
    )


def _type_fields(body: DatasourceCreate | DatasourceUpdate | DsTestIn) -> dict[str, Any]:
    """前端 save() 的 extra：只保留当前类型用得到的字段，其余清空。"""
    t = body.type
    return {
        "compat_mode": (body.compat_mode or DM_COMPAT[0]) if t == DsType.dm8 else None,
        "database_mode": (body.database_mode or KB_MODES[0]) if t == DsType.kingbase else None,
        "erp_system": (body.erp_system or ERP_SYSTEMS[0]) if t == DsType.erp else None,
        "app_id": (body.app_id or "") if t == DsType.erp else None,
    }


async def run_test(kind: DsType, config: dict[str, Any]) -> ConnectionTestResult:
    _require_real(kind)
    return await connector.test_real(kind.value, config)


def _now_text() -> str:
    return clock.local_now().strftime(DATETIME_FORMAT)


class DatasourceService:
    def __init__(self, session) -> None:
        self.session = session
        self.repo = DatasourceRepo(session)
        self.tables = DatasourceTableRepo(session)

    async def search(self, *, type_: DsType | None, kw: str | None, page: PageParams):
        return await self.repo.list_page(self.repo.search_query(type_=type_, kw=kw), page)

    async def get(self, code: str) -> Datasource:
        return await self.repo.get_by_code_or_404(code)

    async def list_tables(self, code: str, kw: str | None, page: PageParams):
        ds = await self.get(code)
        return await self.tables.page_for(ds.id, kw, page)

    async def create(self, body: DatasourceCreate, *, actor: str) -> Datasource:
        code = f"DS-{await self.repo.max_code_no() + 1:03d}"
        ds = Datasource(
            code=code,
            name=body.name,
            type=body.type,
            host=body.host,
            port=body.port or DS_DEFAULT_PORT[body.type.value],
            database=body.database,
            username=body.user,
            password_enc=encrypt_password(body.password),
            ssl=body.ssl,
            owner=body.owner,
            status=DsStatus.untested,
            last_test=None,
            object_types=[],
            sync_mode=body.mode,
            sync_cron=body.cron,
            sync_cron_text=body.cron,  # 前端新建时 cronText = cron
            sync_window="全天",
            sync_cursor_field="updated_at",
            sync_next_run=None,
            table_count=0,
            created_on=clock.today(),
            runs=[],
            **_type_fields(body),
        )
        await self.repo.create(ds, actor=actor)
        return ds

    async def update(self, code: str, body: DatasourceUpdate) -> Datasource:
        ds = await self.get(code)
        if body.name != ds.name:
            from sqlalchemy import select

            from app.modules.static.models import ObjectType

            if await self.session.scalar(
                select(ObjectType.id).where(ObjectType.datasource_name == ds.name).limit(1)
            ):
                raise AppError(
                    "旧名称映射尚在使用，请先通过变更单将来源改为连接编号", code="DS_IN_USE", status_code=409
                )
        values: dict[str, Any] = {
            "name": body.name,
            "type": body.type,
            "host": body.host,
            "port": body.port or DS_DEFAULT_PORT[body.type.value],
            "database": body.database,
            "username": body.user,
            "ssl": body.ssl,
            "owner": body.owner,
            "sync_mode": body.mode,
            "sync_cron": body.cron,  # 前端编辑只改 sync.mode / sync.cron，cronText 等保持
            **_type_fields(body),
        }
        moved = (ds.host, ds.username) != (values["host"], values["username"]) or (
            body.port is not None and body.port != ds.port
        )
        if body.password:  # 留空 = 不修改
            values["password_enc"] = encrypt_password(body.password)
        elif moved and ds.password_enc:  # 改了地址 / 账号还沿用旧密码，会把密码发到新地址
            raise PasswordRequired()
        return await self.repo.update(ds, values)

    async def delete(self, code: str) -> None:
        from sqlalchemy import select

        from app.modules.data.mappings import guard_delete

        ds = await self.get(code)
        await self.session.scalar(select(Datasource).where(Datasource.id == ds.id).with_for_update())
        await guard_delete(self.session, ds)
        await self.session.delete(ds)
        await self.session.flush()

    async def test_saved(self, code: str) -> DsTest:
        """用已保存的配置测试，更新 status / lastTest（前端 testConn：失败不改状态，成功 → 正常）。"""
        ds = await self.get(code)
        _require_real(ds.type)
        config: dict[str, Any] = {
            "host": ds.host,
            "port": ds.port,
            "database": ds.database,
            "user": ds.username,
            "ssl": ds.ssl,
            "password": decrypt_password(ds.password_enc),
        }
        if not config["password"]:
            return DsTest(
                time=_now_text(), ok=False, ms=0, msg="未保存密码（或密钥已更换），未执行测试；请重新填写密码"
            )
        res = await run_test(ds.type, config)
        test = DsTest(time=_now_text(), ok=res.ok, ms=res.latency_ms, msg=res.message)
        values: dict[str, Any] = {"last_test": test.model_dump()}
        if res.ok:
            values["status"] = DsStatus.ok
        await self.repo.update(ds, values)
        if res.ok:
            try:
                async with self.session.begin_nested():  # 内省出错只回滚这一步，会话还能写测试结果
                    await self.refresh_tables(code, config=config)
            except Exception as e:
                test.msg = f"{test.msg}；库表清单刷新失败：{connector.err_text(e, config.get('password'))}"
                await self.repo.update(ds, {"last_test": test.model_dump()})
        return test

    async def refresh_tables(self, code: str, *, config: dict[str, Any] | None = None) -> int:
        """连上源库内省，同步 datasource_table：

        - 按 (schema, 表名) 对齐；已有行保留映射（object_type / ref），更新说明、字段数、行数、最近更新；
        - 源库新出现的表加进来（未映射）；源库没了的表：未映射的删掉，已映射的保留（映射要人处理）；
        - seq 重排 = 已映射在前，再按 schema + 表名（同 mock dsTables）；table_count 同步。
        返回内省到的表数。
        """
        ds = await self.get(code)
        if not _use_real(ds.type) or not connector.supports(ds.type.value):
            raise RealConnectUnavailable(details={"type": ds.type.value})
        if config is None:
            config = {
                "host": ds.host,
                "port": ds.port,
                "database": ds.database,
                "user": ds.username,
                "ssl": ds.ssl,
                "password": decrypt_password(ds.password_enc),
            }
        try:
            found = await connector.list_tables(ds.type.value, config)
        except Exception as e:
            raise IntrospectFailed(f"连接源库失败：{connector.err_text(e, config.get('password'))}") from None
        now = clock.now()
        rows = list(await self.tables.all_for(ds.id))
        from app.modules.data.mappings import mapped_objects

        mapped = await mapped_objects(self.session, ds)
        mapped_tables = {o.source_table for o in mapped}
        by_key = {(t.schema_name, t.name): t for t in rows}
        seen: set[tuple[str, str]] = set()
        for info in found:
            key = (info.schema, info.name)
            seen.add(key)
            t = by_key.get(key)
            if t is None:
                t = DatasourceTable(
                    datasource_id=ds.id,
                    seq=0,
                    schema_name=info.schema,
                    name=info.name,
                    object_type=None,
                    ref=None,
                    comment="",
                    row_count=0,
                    col_count=0,
                    meta_updated_at=now,
                )
                self.session.add(t)
                by_key[key] = t
            t.comment, t.col_count, t.row_count, t.meta_updated_at = info.comment, info.cols, info.rows, now
        for key, t in list(by_key.items()):
            if (
                key not in seen
                and not t.object_type
                and (
                    f"{t.schema_name}.{t.name}" not in mapped_tables
                    and (t.schema_name != "public" or t.name not in mapped_tables)
                )
            ):
                await self.session.delete(t)
                del by_key[key]
        ordered = sorted(by_key.values(), key=lambda t: (t.object_type is None, t.schema_name, t.name))
        for i, t in enumerate(ordered):
            t.seq = i
        ds.table_count = len(ordered)
        await self.session.flush()
        return len(found)

    async def test_unsaved(self, body: DsTestIn) -> DsTest:
        config: dict[str, Any] = {
            "host": body.host,
            "port": body.port or DS_DEFAULT_PORT[body.type.value],
            "database": body.database,
            "user": body.user,
            "password": body.password or "",
            "ssl": body.ssl,
            **_type_fields(body),
        }
        res = await run_test(body.type, config)
        return DsTest(time=_now_text(), ok=res.ok, ms=res.latency_ms, msg=res.message)
