"""The isolated UI profile can append only the one authorized QA action grant."""

import json
from types import SimpleNamespace

from scripts.retail_workflow_ui_server import ORDER_ACTION, order_permission_payload


def test_exact_order_action_grant_only():
    role = SimpleNamespace(lock_version=1, func={"action-center": "manage"}, objs=[
        {"api": api, "objType": api, "view": True, "export": True,
         "actions": ["wf_record_review"]} for api in ("store", "order")])
    body = {"func": role.func, "objs": json.loads(json.dumps(role.objs)), "lockVersion": 1}
    body["objs"][1]["actions"].append(ORDER_ACTION)
    assert order_permission_payload(body, role)
    for field, value in (("lockVersion", True), ("lockVersion", 2), ("func", {})):
        assert not order_permission_payload({**body, field: value}, role)
    for api, field, value in (("store", "actions", ["wf_record_review", ORDER_ACTION]),
                              ("order", "export", False),
                              ("order", "actions", ["wf_record_review", "another_action"])):
        changed = json.loads(json.dumps(body))
        next(entry for entry in changed["objs"] if entry["api"] == api)[field] = value
        assert not order_permission_payload(changed, role)
    assert role.objs[1]["actions"] == ["wf_record_review"]
