from tests.fixtures.section_mods import BETA

SECTION = BETA


class BetaProbe:
    section = SECTION

    async def build_section(self, session, base, change_order_ids):
        return base or {}

    def seed_section(self, ver, ctx):
        return {"probe_event": {"ver": ver}}


def register() -> BetaProbe:
    from app.modules.versioning.sections import register_section, registered_sections

    existing = registered_sections().get(SECTION)
    if isinstance(existing, BetaProbe):
        return existing
    b = BetaProbe()
    register_section(b)
    return b
