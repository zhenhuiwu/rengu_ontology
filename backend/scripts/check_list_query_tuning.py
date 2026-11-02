"""SQL compilation and fake-session assertions only; no database or network access."""

import socket
from types import SimpleNamespace


def refuse_network(*args, **kwargs):
    raise AssertionError("This check must not connect to a network")


socket.socket.connect = refuse_network
socket.socket.connect_ex = refuse_network

from fastapi import Request  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.common.pagination import PageParams  # noqa: E402
from app.modules.ontology.router import workspace_directory  # noqa: E402
from app.modules.static.read_pages import object_list  # noqa: E402


def sql(stmt):
    return str(stmt.compile(dialect=postgresql.dialect()))


class ObjectRepo:
    async def page(self, stmt, params, **kwargs):
        query = sql(stmt)
        columns = query.split("\nFROM", 1)[0]
        assert "object_type.sample_questions" not in columns
        assert "object_type.description" not in columns
        assert "object_type.record_count" in columns
        assert "WHERE" in query and "ORDER BY object_type.seq, object_type.id" in query
        assert kwargs["focus_id"] == "example" and str(kwargs["key"]) == "ObjectType.code"
        row = SimpleNamespace(id=7, code="example", display_name="Example", api_name="Example",
                              obj_group="Examples", source_table=None, record_count=1234,
                              status=SimpleNamespace(value="已发布"))
        return [row], 1, params.clamp(1)

    async def prop_counts(self, ids):
        assert ids == [7]
        return {7: 2}


class DirectorySession:
    def __init__(self, total, *, member=True):
        self.total, self.member, self.queries = total, member, []

    async def scalar(self, stmt):
        query = sql(stmt)
        self.queries.append(query)
        if "FROM sys_user" in query:
            return 17
        assert "ontology_workspace.deleted_at IS NULL" in query
        assert "ontology_member.user_id" in query
        return self.total if "count(*)" in query else 1 if self.member else None

    async def execute(self, stmt):
        query = sql(stmt)
        self.queries.append(query)
        assert "ontology_workspace.deleted_at IS NULL" in query
        assert "ontology_member.user_id" in query
        workspace = SimpleNamespace(id=23, code="example", name="Example", description="",
                                    domain="", is_default=False, workers_paused=False)
        return [(workspace, "owner")] * (min(self.total, 5) if "LIMIT" in query else self.total)


async def main():
    page = await object_list(SimpleNamespace(ver=None, repo=ObjectRepo()), "Example", PageParams(), "example")
    assert page["items"][0]["props"] == 2 and page["items"][0]["count"] == "1,234"
    request = Request({"type": "http", "headers": [(b"x-ontology-id", b"23")]})
    user = SimpleNamespace(id="example", func={"releases": "manage"})
    for requested_page, total, member in ((None, 25, True), (99, 25, True), (None, 0, False), (99, 0, False)):
        session = DirectorySession(total, member=member)
        result = await workspace_directory(session, user, request, requested_page, 10)
        assert sum("FROM sys_user" in q for q in session.queries) == 1
        assert sum("count(*)" in q for q in session.queries) == (requested_page is not None)
        assert result["total"] == total and result["canCreate"]
        assert result["page"] == (3 if requested_page and total else 1)
        assert result["size"] == (10 if requested_page is not None else total)
        assert result["currentOntologyId"] == (23 if member else None)
    print("list query tuning: offline assertions passed")


if __name__ == "__main__":
    # Fake sessions finish inline; avoiding a Windows event loop also avoids its loopback socketpair.
    check = main()
    try:
        check.send(None)
    except StopIteration:
        pass
    else:
        raise AssertionError("This check must not wait for I/O")
