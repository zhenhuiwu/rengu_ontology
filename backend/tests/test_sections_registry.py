"""快照 section 的唯一登记点（load_sections）和测试隔离接口。

测试隔离接口：registry_snapshot / registry_restore / isolated_sections。
"""

import pytest

from app.modules.versioning import sections as reg
from app.modules.versioning.sections import (
    isolated_sections,
    load_sections,
    register_section,
    registered_sections,
    registry_restore,
    registry_snapshot,
)

PKG = "tests.fixtures.section_mods"


class _Probe:
    def __init__(self, section: str) -> None:
        self.section = section

    async def build_section(self, session, base, change_order_ids):
        return base

    def seed_section(self, ver, ctx):
        return {}


def test_load_sections_discovers_builders_and_register_and_is_idempotent():
    from tests.fixtures.section_mods.alpha.sections import BUILDERS, TermsProbe

    assert registered_sections() == {}  # conftest：每个测试从空注册表开始
    assert load_sections([PKG]) == ["terms", "eventTypes"]  # alpha（BUILDERS）、beta（register()）；gamma 无
    first = registered_sections()
    assert first["terms"] is BUILDERS[0]
    assert load_sections([PKG]) == ["terms", "eventTypes"]  # 再来一遍：同一实例，放行
    assert registered_sections() == first
    with pytest.raises(ValueError, match="已经登记过"):  # 同一 section 换个实例 → 冲突
        register_section(TermsProbe())


def test_load_sections_default_scans_app_modules(monkeypatch):
    """默认扫 settings.section_packages = ["app.modules"]；versioning 自己的 sections（注册表本身）跳过。"""
    from app.core.config import get_settings

    assert get_settings().section_packages == ["app.modules"]
    seen = []
    real = reg.importlib.import_module

    def spy(name, *a, **kw):
        seen.append(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(reg.importlib, "import_module", spy)
    load_sections()
    assert seen[0] == "app.modules"
    assert "app.modules.versioning.sections" not in seen
    assert all(n.endswith(".sections") for n in seen[1:])


def test_create_app_and_seed_cli_call_load_sections(monkeypatch):
    from typer.testing import CliRunner

    import app.main as main
    from app.cli import cli

    calls = []
    monkeypatch.setattr(main, "load_sections", lambda: calls.append("app"))
    main.create_app()
    assert calls == ["app"]

    import app.modules.versioning.sections as sections_mod

    monkeypatch.setattr(sections_mod, "load_sections", lambda: calls.append("cli"))

    async def _no_seed(*a, **kw):
        raise SystemExit(0)  # 只验证 seed 命令先登记 section，不真的连库

    monkeypatch.setattr("app.seeds.loader.run_seeds", _no_seed)
    CliRunner().invoke(cli, ["seed", "--dry-run"])
    assert calls == ["app", "cli"]


def test_registry_snapshot_and_restore():
    a, b = _Probe("rules"), _Probe("lifecycles")
    register_section(a)
    snap = registry_snapshot()
    register_section(b)
    reg.unregister_section("rules")
    assert list(registered_sections()) == ["lifecycles"]
    registry_restore(snap)
    assert registered_sections() == {"rules": a}
    assert registry_snapshot() == snap  # 不可变副本（tuple），可以比较


def test_isolated_sections_restores_even_on_error():
    keep = _Probe("rules")
    register_section(keep)
    with isolated_sections(_Probe("metrics")):
        assert list(registered_sections()) == ["metrics"]  # 默认先清空
    assert registered_sections() == {"rules": keep}
    with isolated_sections(_Probe("metrics"), empty=False):
        assert list(registered_sections()) == ["rules", "metrics"]
    with pytest.raises(RuntimeError), isolated_sections():
        load_sections([PKG])
        assert list(registered_sections()) == ["terms", "eventTypes"]
        raise RuntimeError("测试出错")
    assert registered_sections() == {"rules": keep}  # 发现的 section 也随块退出撤掉
    with pytest.raises(ValueError), isolated_sections(_Probe("nope")):  # 名字照常校验
        pass
    assert registered_sections() == {"rules": keep}
