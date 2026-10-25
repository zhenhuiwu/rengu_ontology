"""快照 section 注册表（契约见 app/contracts/versioning.py 第 4、5 条）。

**唯一登记点**：负责 section 的模块把 builder 放在 `app/modules/<模块>/sections.py`，导出

    BUILDERS = [RulesSection(), LifecyclesSection()]   # 模块级实例，按此顺序登记

（或者导出一个幂等的 `register()` 函数）。骨架的 load_sections() 扫描所有 `app.modules.*.sections`，
在 create_app() 和 `python -m app.cli seed` 开头调用；模块自己不用在 __init__ 里登记，也不用改 main / cli。
版本层只按登记顺序调用，不理解 section 内容。
"""

import importlib
import importlib.util
import logging
import pkgutil
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.versioning import SECTION_OWNERS, RestorableSection, Snapshot, SnapshotSectionBuilder

_registry: dict[str, SnapshotSectionBuilder] = {}
log = logging.getLogger(__name__)


def register_section(builder: SnapshotSectionBuilder) -> None:
    if builder.section not in SECTION_OWNERS:
        raise ValueError(f"未知的快照 section {builder.section!r}；先在 SECTION_OWNERS 里登记负责模块")
    if builder.section in _registry and _registry[builder.section] is not builder:
        raise ValueError(f"快照 section {builder.section!r} 已经登记过")
    _registry[builder.section] = builder


def unregister_section(section: str) -> None:
    _registry.pop(section, None)


def registered_sections() -> dict[str, SnapshotSectionBuilder]:
    return dict(_registry)


def load_sections(packages: list[str] | None = None) -> list[str]:
    """发现并登记所有模块的快照 section；返回登记后注册表里的 section 名（登记顺序）。

    对 packages（默认 settings.section_packages = ["app.modules"]）下每个子包，若存在 `<子包>.sections`：
    - 有 `BUILDERS`（builder 实例列表）→ 逐个 register_section；
    - 否则有可调用的 `register()` → 调用它（须幂等，例如已登记同类实例就直接返回）；
    - 都没有 → 只 import（兼容「import 时登记」的旧写法；注意 import 只发生一次）。
    幂等：同一个实例重复登记放行；同一 section 换了另一个实例照旧报 ValueError。
    子包按名字排序扫描，所以登记顺序稳定（同一模块内按 BUILDERS 顺序）。
    """
    from app.core.config import get_settings

    for pkg_name in packages or get_settings().section_packages:
        pkg = importlib.import_module(pkg_name)
        for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
            name = f"{pkg_name}.{info.name}.sections"
            if not info.ispkg or name == __name__ or importlib.util.find_spec(name) is None:
                continue
            mod = importlib.import_module(name)
            builders = getattr(mod, "BUILDERS", None)
            if builders is not None:
                for b in builders:
                    register_section(b)
            elif callable(getattr(mod, "register", None)):
                mod.register()
    names = list(_registry)
    log.info("快照 section 已登记：%s", ", ".join(names) or "（无）")
    return names


# ---- 测试隔离用的公开接口（模块测试不要直接碰 _registry）----
RegistrySnapshot = tuple[tuple[str, SnapshotSectionBuilder], ...]


def registry_snapshot() -> RegistrySnapshot:
    """注册表的不可变副本（保留登记顺序；顺序决定 build / restore 的调用顺序）。"""
    return tuple(_registry.items())


def registry_restore(snap: RegistrySnapshot) -> None:
    """把注册表整体恢复成 registry_snapshot() 的结果（先清空，再按原顺序放回；不做重名检查）。"""
    _registry.clear()
    _registry.update(snap)


@contextmanager
def isolated_sections(*builders: SnapshotSectionBuilder, empty: bool = True) -> Iterator[None]:
    """测试用：块内注册表隔离，退出时（含异常）恢复原样。

    empty=True（默认）先清空，再登记 builders（经 register_section，照常校验 section 名）；
    empty=False 在现有登记的基础上追加。conftest 的 autouse fixture 就是 `with isolated_sections(): yield`。
    """
    saved = registry_snapshot()
    try:
        if empty:
            _registry.clear()
        for b in builders:
            register_section(b)
        yield
    finally:
        registry_restore(saved)


class SectionSnapshotProvider:
    """默认 SnapshotProvider：已登记的 section 交给各自 builder，未登记的 section 原样沿用上一版。"""

    async def build_snapshot(
        self, session: AsyncSession, base_ver: str | None, change_order_ids: list[str]
    ) -> Snapshot:
        from app.modules.versioning.service import VersioningService

        base = (await VersioningService(self).get_snapshot(session, base_ver) if base_ver else None) or {}
        out: Snapshot = dict(base)
        for name, builder in _registry.items():
            out[name] = await builder.build_section(session, base.get(name), list(change_order_ids))
        return out


async def restore_sections(session: AsyncSession, snapshot: Snapshot, release_id: int) -> None:
    """整版回滚：同一事务里让实现了 restore_section 的 builder 把定义表恢复成 snapshot 里的那一节。

    由 create_version 在**已写入**这次回滚的新版本行之后调用；release_id = 那一行的 ontology_release.id
    （Palantir 用它写 retired_release_id，action-event-api.md:325）。
    按登记顺序逐节调用，任一节抛错整个发布回滚。
    被恢复版本没有这一节（缺失或 null）→ 不调用钩子、定义表不动（静态裁定，和动态一致）[推断]；
    空内容（{} / []）照样调用，由模块把定义表恢复成「空」（静态全部置失效，动态 version 置空）。
    """
    for name, builder in _registry.items():
        content = snapshot.get(name)
        if isinstance(builder, RestorableSection) and content is not None:
            await builder.restore_section(session, content, release_id=release_id)


def seed_snapshot(ver: str, ctx: Any) -> Snapshot:
    """seed 用：按登记的 builder 组装历史版本 ver 的快照；不允许空快照。

    没有登记任何 section 时 versioning seed 用 skip_if 整条跳过、不会走到这里；这里的报错只防直接调用。
    """
    if not _registry:
        raise RuntimeError(
            f"没有任何快照 section 登记，版本 {ver} 的 seed 不写空快照；"
            "静态 / 动态 / 动作事件模块实现 SnapshotSectionBuilder 并 register_section 之后再跑"
        )
    snap = {name: b.seed_section(ver, ctx) for name, b in _registry.items()}
    if not any(v for v in snap.values()):
        raise RuntimeError(f"版本 {ver} 的快照各 section 都是空的")
    return snap
