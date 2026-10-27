"""Internal rule subscription entry point. No HTTP endpoint, execution or commit."""


def lifecycle_transition_pairs(
    snapshot: dict, *, object_kind: str, version: int
) -> frozenset[tuple[str, str]]:
    """Read a complete, exact-version transition set from the caller's bound release.

    This checks structural pair membership only, not guards or migration effects.
    The caller must verify object identity/API, permissions and actual state inputs.
    Missing or inconsistent definitions raise RULE_EVALUATION_UNAVAILABLE.
    """
    from app.modules.dynamic.event_rule_runtime import transition_pairs

    return transition_pairs(snapshot, object_kind=object_kind, version=version)


async def dispatch_event_rule(session, user, *, rule_code: str, event_id: str) -> dict:
    """Evaluate a retained production event and atomically persist its hit/alert.

    The caller supplies a resolved deployment identity and owns the transaction.
    Input/definition/version/permissions are reread; payload and natural-language
    subscription instructions cannot select a rule or execution identity.
    """
    from app.modules.dynamic.event_rule_runtime import dispatch

    async with session.begin_nested():
        return await dispatch(session, user, rule_code=rule_code, event_id=event_id)
