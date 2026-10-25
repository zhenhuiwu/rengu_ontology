SECTION = "eventTypes"


class EventTypesProbe:
    section = SECTION

    async def build_section(self, session, base, change_order_ids):
        return base or {}

    def seed_section(self, ver, ctx):
        return {"probe_event": {"ver": ver}}


def register() -> EventTypesProbe:
    from app.modules.versioning.sections import register_section, registered_sections

    existing = registered_sections().get(SECTION)
    if isinstance(existing, EventTypesProbe):
        return existing
    b = EventTypesProbe()
    register_section(b)
    return b
