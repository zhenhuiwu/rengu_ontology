"""Retained checks only: no server, subprocess, database or worker is started."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from app.modules.ontology.router import RoleBindingsPatchIn, patch_object_roles


def test_dev_launcher_defaults_to_api_and_worker_selection_keeps_high_watermark():
    path = Path(__file__).resolve().parents[1] / "run_dev.py"
    spec = importlib.util.spec_from_file_location("local_dev_launcher_check", path)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    arguments = SimpleNamespace(
        host="127.0.0.1", port=8000, with_worker=False,
        ontology=2, event_type="test_event", subscriber="single", after_id=100,
    )
    commands = launcher.commands(arguments)
    assert len(commands) == 1 and "app.main:app" in commands[0]
    assert "--workers" not in commands[0]
    arguments.with_worker = True
    commands = launcher.commands(arguments)
    assert len(commands) == 2
    assert commands[1][1:3] == ["-m", "app.modules.event.worker"]
    assert commands[1][-2:] == ["--after-id", "100"]
    assert "--install-schema" not in commands[1]


async def test_role_patch_changes_only_named_bindings_and_keeps_owner_gate():
    body = RoleBindingsPatchIn(add=["new", "new"], remove=["old"])
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=2),
        scalars=AsyncMock(side_effect=[
            [SimpleNamespace(id=4, code="new"), SimpleNamespace(id=5, code="old")],
            [9], ["existing", "new"],
        ]),
        execute=AsyncMock(), add_all=Mock(), flush=AsyncMock(),
    )
    user = SimpleNamespace(id="owner")
    with patch("app.modules.ontology.router.require_owner", new_callable=AsyncMock) as owner:
        result = await patch_object_roles(2, body, session, user)
    owner.assert_awaited_once_with(session, 2, user)
    assert result == {"roleCodes": ["existing", "new"]}
    additions = session.add_all.call_args.args[0]
    assert [(row.ontology_id, row.role_id) for row in additions] == [(2, 4)]
    deletion = session.execute.call_args.args[0].compile().params
    assert deletion["ontology_id_1"] == 2
    assert deletion["role_id_1"] == [5]
    try:
        RoleBindingsPatchIn(add=["same"], remove=["same"])
    except ValidationError:
        pass
    else:
        raise AssertionError("Conflicting role changes were accepted")
