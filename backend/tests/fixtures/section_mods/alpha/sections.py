from tests.fixtures.section_mods import ALPHA


class AlphaProbe:
    section = ALPHA

    async def build_section(self, session, base, change_order_ids):
        return {**(base or {}), "probe": {"applied": list(change_order_ids)}}

    def seed_section(self, ver, ctx):
        return {"probe": {"since": ver}}


BUILDERS = [AlphaProbe()]
