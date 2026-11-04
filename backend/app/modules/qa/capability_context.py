"""Share one capability read within an answer while preserving readable-only views."""

from copy import deepcopy

from app.common.ontology import require_ontology_id
from app.modules.qa.read_cache import cached_read
from app.modules.static.capabilities import capabilities as _capabilities


async def capabilities(session, user, request=None, *, readable_only=False):
    key = ("qa_capabilities", id(session), require_ontology_id(session), id(user), id(request))
    catalog = deepcopy(
        await cached_read(key, lambda: _capabilities(session, user, request, readable_only=False))
    )
    # _objects returns the same access cache and object map in both modes.
    if readable_only and "objects" in catalog:
        catalog["objects"] = [o for o in catalog["objects"] if o.get("rowAccess") != "none"]
    return catalog
