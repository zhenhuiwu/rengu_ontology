"""Opt-in inbox ownership self-check. No database, HTTP, notifications or CR submissions."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.errors import Forbidden
from app.modules.notifications.models import Notification
from app.modules.notifications.service import own_scope


class Session:
    info = {"ontology_id": 2}

    async def scalar(self, statement):
        sql = str(statement)
        assert "ontology_member" in sql and "sys_user.status" in sql
        assert 2 in statement.compile().params.values()
        return 7


async def main():
    user = SimpleNamespace(ontology_id=2, ontology_ids=None, is_service_account=False, id="test-only-user")
    scope = await own_scope(Session(), user)
    statement = select(Notification.id).where(*scope)
    values = statement.compile().params.values()
    assert "notification.ontology_id" in str(statement) and 2 in values
    assert "notification.recipient_id" in str(statement) and 7 in values
    for ontology, key_scope in [(1, None), (2, (2,))]:
        user.ontology_id, user.ontology_ids = ontology, key_scope
        try:
            await own_scope(Session(), user)
        except Forbidden:
            pass
        else:
            raise AssertionError("Mismatched workspace or metadata API key reached the inbox")


if __name__ == "__main__":
    asyncio.run(main())
