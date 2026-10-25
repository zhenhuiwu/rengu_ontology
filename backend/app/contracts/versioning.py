"""契约 1：本体整版发布与版本化。

规则（团队约定）：
1. 静态模块发布时调用 `VersionService.create_version(session, change_order_ids, ...)`；
   版本层在**调用方同一个事务**里写入整版快照，返回新版本号（如 "v2026.10.4"）。
   调用方负责同一事务内把变更单置为「已发布」并 commit；任何一步失败整体回滚，版本号不会被占用。
2. 版本只追加、不可修改；“当前版本”= 最新一条，没有可移动的指针。
3. `?ver=` 只读地读取那一版的快照（`get_snapshot(ver)`）。
4. **只有一个入口** `create_version(session, change_order_ids, *, rollback_of=None, ...)`。
   整版回滚 = 同一个入口传 `rollback_of=<被回滚的版本号>`（必须是当前最新版，否则 409 ROLLBACK_NOT_LATEST），
   change_order_ids = 那张回滚变更单：生成一个**新版本**，**不调用** build_section，快照原样复制被回滚
   版本的上一版（change_summary.restoredFrom），记录 rollback_of。仍然只追加，绝不移动指针、不改旧版本。
   定义表（静态的对象 / 指标…、动态 rule 当前版本 / lifecycle.version、Palantir 动作类型…）也要回到那一版：
   版本层**先写入回滚产生的新版本行**，再在同一事务里按登记顺序对每个 builder 调可选钩子
   `restore_section(session, content, *, release_id)`（RestorableSection；
   content = 被恢复版本这一节的快照内容，
   release_id = 这次回滚新版本的 ontology_release.id，例如 Palantir 写 retired_release_id，
   action-event-api.md:325）；没实现钩子的 section 只回快照、不动定义表。
   被恢复版本没有这一节（content 为 None）时版本层**不调用**钩子、定义表不动 [推断]；空内容 {} 照样调用。
   和前端 Releases 页一致：「回滚会生成一个新版本，内容等于上一版本；只能回滚最近一次发布」。
5. 快照 = {section 名: section 内容}。每个 section 由**负责它的模块**组装（SECTION_OWNERS）：
   静态 objectTypes/properties/links/metrics/dimensions/terms/qaPaths；动态 rules/lifecycles
   （lifecycles 按对象类型前端 id 作键）；Palantir actionTypes/eventTypes。
   section 内按**前端 id / API 名**作键（如 "order"、"gross_margin"、"R-007"）。
   各模块实现 SnapshotSectionBuilder（build_section + seed_section），按 section 在 versioning 的注册表登记
   （app/modules/versioning/sections.py 的 register_section）；create_version 依次调用已登记的 builder，
   未登记的 section 原样沿用上一版。
   版本层把 section 内容当不透明 JSON：不解析、不校验结构，只负责组装顺序、存储、校验和（sha256）、
   版本号和并发控制（全局发布锁）。
6. 发布时定义表的展开（把变更单内容写进各定义表）由静态在**同一个发布事务里同步完成**，不走异步任务。
7. 版本号 vYYYY.MM.N 由 create_version 生成（同月递增，跨月从 1 开始），调用方不传。
8. 整版回滚 = 新版本 + 回滚变更单；没有「已回滚」状态，旧版本不改。
9. 变更单号 CR-NNNN 是**一个全局序列**（不按天重置），归静态模块；动态的规则、动作、事件的变更单也用它。
   版本层只记录变更单号，不分配。
10. seed：历史版本的快照由各 builder 的 `seed_section(ver, ctx)` 组装（读 export-mock 的 JSON），
    **不写空快照 {}**：没有任何 builder 登记、或拼出来是空的，版本 seed 直接报错。
    seed 顺序里 versioning 在 static 之前（静态的 release_id 等外键按 ver 查 ontology_release.id）。
11. 函数（Functions）**不走**本契约：单独存 fn_version 表（按函数各自递增版本，static 模块负责）。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

Snapshot = dict[str, Any]

# 快照 section → 负责组装的模块（文档 / 登记校验用；版本层不理解内容）
SECTION_OWNERS: dict[str, str] = {
    "objectTypes": "static",
    "properties": "static",
    "links": "static",
    "metrics": "static",
    "dimensions": "static",
    "terms": "static",
    "qaPaths": "static",
    "rules": "dynamic",
    "lifecycles": "dynamic",
    "actionTypes": "action",  # Palantir 负责（action 模块）
    "eventTypes": "event",  # Palantir 负责（event 模块）
}


@dataclass(frozen=True)
class ReleaseInfo:
    ver: str
    parent_ver: str | None
    published_at: datetime
    published_by: str  # 发布人姓名（接口 by）
    note: str
    change_order_ids: tuple[str, ...]
    snapshot_sha256: str
    rollback_of: str | None = None
    published_by_code: str | None = None  # 发布人 sys_user.code（接口 byId）


@runtime_checkable
class SnapshotSectionBuilder(Protocol):
    """各模块实现自己负责的 section（section 名见 SECTION_OWNERS）。

    - build_section：发布事务里同步调用。base 是上一版这个 section 的内容（没有则 None），
      返回应用这些变更单后的完整 section（变更单不涉及本 section 时原样返回 base）；
      定义表的同步展开也在这里（同一事务）完成，不走异步任务。
    - seed_section：seed 时为 mock 里的历史版本 ver 组装 section（ctx 是 app.seeds.SeedContext，
      `ctx.load("RULE_DEFS.json", group="dynamic")` 之类）。
    """

    section: str

    async def build_section(
        self, session: AsyncSession, base: Any | None, change_order_ids: list[str]
    ) -> Any: ...

    def seed_section(self, ver: str, ctx: Any) -> Any: ...

    # 可选：整版回滚钩子见 RestorableSection（同一个 builder 类上实现 restore_section 即可）。


@runtime_checkable
class RestorableSection(Protocol):
    """可选：整版回滚时把本模块的定义表恢复成 content。不实现 = 只回快照、不动定义表。

    - 调用时机：create_version(rollback_of=…) 已经写入这次回滚的新版本行（已 flush，有 id）之后，
      同一事务、按 section 登记顺序；钩子抛错 → 整个回滚（含新版本行、outbox）一起回滚。
    - content：被恢复版本（rollback_of 的上一版）快照里这一节，原样 JSON；不会是 None
      （那一版没有这一节时不调用）；{} / [] 照样调用。
    - release_id（keyword-only）：这次回滚新版本的 ontology_release.id（不是版本号）。
      用途如 action_type.retired_release_id
      = release_id（快照里没有的类型下线，action-event-api.md:325）；需要版本号可按 id 查 ontology_release。
    """

    section: str

    async def restore_section(self, session: AsyncSession, content: Any, *, release_id: int) -> None: ...


@runtime_checkable
class SnapshotProvider(Protocol):
    """组装整版快照。默认实现 = 按注册表依次调用各模块的 SnapshotSectionBuilder（版本层提供）。"""

    async def build_snapshot(
        self, session: AsyncSession, base_ver: str | None, change_order_ids: list[str]
    ) -> Snapshot: ...


@runtime_checkable
class VersionService(Protocol):
    """本体小白实现（app/modules/versioning/service.py）。"""

    async def create_version(
        self,
        session: AsyncSession,
        change_order_ids: list[str],
        *,
        rollback_of: str | None = None,
        published_by: str,
        note: str,
    ) -> str:
        """唯一入口：同事务写快照，返回新版本号，不 commit。

        rollback_of=None：调用各 section builder 组装；
        rollback_of=<当前最新版本号>：不调 builder，快照原样复制它的上一版，记录回滚变更单和 rollback_of；
        不是最新版 → 409 ROLLBACK_NOT_LATEST。
        published_by = 用户编号（CurrentUser.id = sys_user.code，如 U010001），库里存 sys_user.id 外键。
        同一事务写 outbox 事件 `release.published`（发布和回滚都写），payload
        `{ver, parentVer, kind: "release"|"rollback", rollbackOf, changeOrderIds}`；其它模块只订阅
        （app.common.outbox.subscribe），不要在发布流程里直接调别的模块。
        """
        ...

    async def current_version(self, session: AsyncSession) -> str | None: ...

    async def get_release(self, session: AsyncSession, ver: str) -> ReleaseInfo | None: ...

    async def get_snapshot(self, session: AsyncSession, ver: str | None = None) -> Snapshot | None:
        """ver=None 表示当前版本。"""
        ...
