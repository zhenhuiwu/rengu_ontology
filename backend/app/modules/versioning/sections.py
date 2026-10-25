"""快照 section 注册表（契约见 app/contracts/versioning.py 第 5、10 条）。

各模块在自己的包里登记 builder，例如动态：

    from app.modules.versioning.sections import register_section
    register_section(RulesSection())          # section = "rules"

版本层只按登记顺序调用，不理解 section 内容。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.versioning import SECTION_OWNERS, Snapshot, SnapshotSectionBuilder

_registry: dict[str, SnapshotSectionBuilder] = {}


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


async def restore_sections(session: AsyncSession, snapshot: Snapshot) -> None:
    """整版回滚：同一事务里让实现了 restore_section 的 builder 把定义表恢复成 snapshot 里的那一节。

    被恢复版本没有这一节（缺失或 null）→ 不调用钩子、定义表不动（静态裁定，和动态一致）[推断]；
    空内容（{} / []）照样调用，由模块把定义表恢复成「空」（静态全部置失效，动态 version 置空）。
    """
    for name, builder in _registry.items():
        hook = getattr(builder, "restore_section", None)
        content = snapshot.get(name)
        if hook is not None and content is not None:
            await hook(session, content)


def seed_snapshot(ver: str, ctx: Any) -> Snapshot:
    """seed 用：按登记的 builder 组装历史版本 ver 的快照；不允许空快照。"""
    if not _registry:
        raise RuntimeError(
            f"没有任何快照 section 登记，版本 {ver} 的 seed 不写空快照；"
            "静态 / 动态 / 动作事件模块实现 SnapshotSectionBuilder 并 register_section 之后再跑"
        )
    snap = {name: b.seed_section(ver, ctx) for name, b in _registry.items()}
    if not any(v for v in snap.values()):
        raise RuntimeError(f"版本 {ver} 的快照各 section 都是空的")
    return snap
