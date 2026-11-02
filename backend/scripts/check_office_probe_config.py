"""Offline exact local-probe ceiling check; no HTTP, database or worker."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from pydantic import SecretStr
from app.core.errors import AppError
from app.modules.integration import adapters
from app.modules.integration.models import ExtSystemStatus

target = {"ontology_id": 16, "system_code": "EXT-N01", "origin": "https://127.0.0.1:8767",
          "submit_path": "/office/receipt", "query_path": "/office/status", "probe_path": "/office/health"}
config = {"origin": target["origin"], "submit_path": target["submit_path"], "query_path": target["query_path"], "probe_path": target["probe_path"]}
settings = NS(env="dev", integration_real_connect=True, integration_adapters=SecretStr(json.dumps({"16/EXT-N01": config})),
              integration_local_demo=json.dumps(target), integration_ca_file=None)
system = NS(ontology_id=16, code="EXT-N01", local_only=True, env="测试", category="Webhook",
            status=ExtSystemStatus.ok, endpoint=target["origin"], http_endpoints=[], probe_path=target["probe_path"])
tree = ast.parse((Path(__file__).resolve().parents[1] / "app/modules/integration/router.py").read_text("utf-8"))
guard = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and ast.unparse(node.test) == "probe and system.local_only")
guard_code = compile(ast.Module(body=[guard], type_ignores=[]), "local-probe-save-guard", "exec")


def rejected(callback):
    try:
        callback()
    except AppError:
        return
    raise AssertionError("unconfigured or mismatched local probe must be rejected")


with patch.object(adapters, "get_settings", return_value=settings):
    assert adapters.get_probe_adapter(system).path("probe") == "/office/health"
    # Saving the first exact probe works before the stored probe exists.
    system.probe_path = None
    exec(guard_code, {"probe": NS(path="/office/health"), "system": system, "AppError": AppError})
    rejected(lambda: exec(guard_code, {"probe": NS(path="/other"), "system": system, "AppError": AppError}))
    system.probe_path = "/other"
    rejected(lambda: adapters.get_probe_adapter(system))
    system.probe_path = "/office/health"
    settings.integration_local_demo = json.dumps({key: value for key, value in target.items() if key != "probe_path"})
    rejected(lambda: adapters.get_probe_adapter(system))
    settings.integration_local_demo = json.dumps({**target, "ontology_id": 17})
    rejected(lambda: adapters.get_probe_adapter(system))
print("Exact local probe save/run ceiling passed; no HTTP or DB.")
