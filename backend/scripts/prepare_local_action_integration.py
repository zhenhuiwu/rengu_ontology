"""Prepare only the workspace-16 office receipt deployment configuration.

First run: python -m scripts.local_office_receiver --prepare
Review: python -m scripts.prepare_local_action_integration --directory <returned directory>
Apply after review: append --apply. Existing unrelated settings are preserved.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import tempfile

from dotenv import dotenv_values, set_key
from app.modules.integration.adapters import AdapterConfig, LocalDemoConfig
from scripts.local_webhook_receiver import private_directory

KEY = "16/EXT-N01"
TARGET = {"ontology_id": 16, "system_code": "EXT-N01", "origin": "https://127.0.0.1:8767",
          "submit_path": "/office/receipt", "query_path": "/office/status", "probe_path": "/office/health"}


def merge(values, adapter):
    if str(values.get("RENGU_INTEGRATION_REAL_CONNECT", "false")).lower() not in {"true", "1", "yes", "on"}:
        raise ValueError("REAL_CONNECT must already be explicitly enabled; this tool never activates other channels")
    entries = json.loads(values.get("RENGU_INTEGRATION_ADAPTERS") or "{}")
    targets = json.loads(values.get("RENGU_INTEGRATION_LOCAL_DEMO") or "[]")
    networks = json.loads(values.get("RENGU_INTEGRATION_PROBE_NETWORKS") or "[]")
    targets = targets if isinstance(targets, list) else [targets]
    if not isinstance(entries, dict) or not isinstance(networks, list):
        raise ValueError("Existing integration configuration is invalid")
    if KEY in entries and entries[KEY] != adapter:
        raise ValueError("Existing office adapter differs; refusing to replace its credentials or contract")
    AdapterConfig.model_validate(adapter)
    desired = LocalDemoConfig.model_validate(TARGET)
    matches = [item for item in targets if (LocalDemoConfig.model_validate(item).ontology_id,
               LocalDemoConfig.model_validate(item).system_code) == (16, "EXT-N01")]
    if len(matches) > 1 or matches and LocalDemoConfig.model_validate(matches[0]) != desired:
        raise ValueError("Existing office local-demo contract differs; refusing to replace it")
    entries[KEY] = adapter
    if not matches:
        targets.append(TARGET.copy())
    network = {"cidr": "127.0.0.1/32", "ports": [8767]}
    if network not in networks:
        networks.append(network)
    return {"RENGU_INTEGRATION_ADAPTERS": json.dumps(entries, separators=(",", ":")),
            "RENGU_INTEGRATION_LOCAL_DEMO": json.dumps(targets, separators=(",", ":")),
            "RENGU_INTEGRATION_PROBE_NETWORKS": json.dumps(networks, separators=(",", ":"))}


def prepare_environment(directory, apply):
    backend = Path(__file__).resolve().parents[1]
    environment = backend / ".env"
    subprocess.run(["git", "check-ignore", "-q", "--", str(environment)], cwd=backend, check=True)
    private_directory(directory)
    original = environment.read_bytes()
    values = dotenv_values(environment)
    adapter = {"origin": TARGET["origin"], "submit_path": TARGET["submit_path"],
               "query_path": TARGET["query_path"], "probe_path": TARGET["probe_path"],
               "actions": ["recordExternalInspection"],
               "token": (directory / "bearer-token.txt").read_text("utf-8"),
               "signing_secret": (directory / "signing-secret.txt").read_text("utf-8")}
    updates = merge(values, adapter)
    old_ca = values.get("RENGU_INTEGRATION_CA_FILE")
    old_chain = b""
    if old_ca:
        previous = Path(old_ca)
        previous = previous if previous.is_absolute() else backend / previous
        old_chain = previous.read_bytes()
    else:
        old_chain = "".join(ssl.DER_cert_to_PEM_cert(cert) for cert in ssl.create_default_context().get_ca_certs(binary_form=True)).encode("ascii")
    office_ca = (directory / "ca.pem").read_bytes()
    chain = old_chain if office_ca.strip() in old_chain else old_chain.rstrip() + b"\n" + office_ca
    bundle = directory / "office-trust-bundle.pem"
    bundle.write_bytes(chain)
    ssl.create_default_context(cafile=str(bundle))
    updates["RENGU_INTEGRATION_CA_FILE"] = str(bundle)
    suffix = secrets.token_hex(6)
    backup = directory / f"backend.env.before-{suffix}"
    proposal = directory / f"backend.env.proposal-{suffix}"
    backup.write_bytes(original)
    proposal.write_bytes(original)
    for name, value in updates.items():
        set_key(str(proposal), name, value, quote_mode="always")
    after = dotenv_values(proposal)
    assert all(after[key] == value for key, value in values.items() if key not in updates)
    manifest = {"scope": 16, "systemCode": "EXT-N01", "modifiedKeys": list(updates),
                "beforeSha256": hashlib.sha256(original).hexdigest(), "backup": str(backup),
                "afterSha256": hashlib.sha256(proposal.read_bytes()).hexdigest(), "applied": False}
    if apply:
        local = backend / ".local"
        subprocess.run(["git", "check-ignore", "-q", "--", str(local / "office.pending")], cwd=backend, check=True)
        local.mkdir(exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="office-env-", dir=local))
        try:
            if os.name == "nt":
                identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], check=True, capture_output=True, text=True)
                sid = next(csv.reader(identity.stdout.strip().splitlines()))[1]
                subprocess.run(["icacls", str(staging), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"], check=True, capture_output=True)
            pending = staging / "environment.pending"
            pending.write_bytes(proposal.read_bytes())
            if environment.read_bytes() != original:
                raise ValueError("Environment changed concurrently; refusing overwrite")
            os.replace(pending, environment)
            manifest["applied"] = True
        finally:
            if (staging / "environment.pending").exists():
                (staging / "environment.pending").unlink()
            staging.rmdir()
    manifest_path = directory / f"environment-change-{suffix}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), "utf-8")
    print(json.dumps({"scope": 16, "systemCode": "EXT-N01", "applied": apply,
                      "proposalFile": str(proposal), "manifestFile": str(manifest_path),
                      "credentialsPrinted": False, "serviceStarted": False}))


def self_check():
    adapter = {"origin": TARGET["origin"], "submit_path": TARGET["submit_path"], "query_path": TARGET["query_path"],
               "probe_path": TARGET["probe_path"], "actions": ["recordExternalInspection"],
               "token": secrets.token_hex(24), "signing_secret": secrets.token_hex(32)}
    old_target = {**TARGET, "ontology_id": 2, "system_code": "OTHER"}
    values = {"RENGU_INTEGRATION_REAL_CONNECT": "true", "RENGU_INTEGRATION_ADAPTERS": '{"2/OTHER":{}}',
              "RENGU_INTEGRATION_LOCAL_DEMO": json.dumps([old_target]), "RENGU_INTEGRATION_PROBE_NETWORKS": "[]"}
    merged = merge(values, adapter)
    assert json.loads(merged["RENGU_INTEGRATION_ADAPTERS"])["2/OTHER"] == {}
    assert json.loads(merged["RENGU_INTEGRATION_LOCAL_DEMO"])[0] == old_target
    assert merge({**values, **merged}, adapter) == merged
    for changed in ({"RENGU_INTEGRATION_REAL_CONNECT": "false"}, {"RENGU_INTEGRATION_ADAPTERS": '{"16/EXT-N01":{}}'}):
        try:
            merge({**values, **changed}, adapter)
        except ValueError:
            pass
        else:
            raise AssertionError("Activation or unrelated replacement must be refused")
    print("Office-only environment merge checks passed; no files, sockets or DB touched.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_check:
        self_check()
    elif arguments.directory:
        prepare_environment(arguments.directory.resolve(), arguments.apply)
    else:
        parser.error("--directory is required; generate private material with local_office_receiver --prepare first")
