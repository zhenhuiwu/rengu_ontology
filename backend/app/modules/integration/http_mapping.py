"""Constrained JSON field mappings for the existing signed POST adapter."""
import re
import math
from app.core.errors import AppError

KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SOURCES = {"ontologyId", "actionRunId", "actionType", "typeVersion", "idemKey", "objKind", "objId"}

def invalid():
    return AppError("HTTP 映射配置无效", code="ACTION_HTTP_MAPPING_INVALID", status_code=422)

def validate_mapping(value, params):
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {"request", "response"}:
        raise invalid()
    request, response = value["request"], value["response"]
    if not isinstance(request, list) or not 1 <= len(request) <= 32 or not isinstance(response, dict) or set(response) != {"status", "receiptId", "reason", "code", "success", "failed", "unknown"}:
        raise invalid()
    seen = set()
    for row in request:
        if not isinstance(row, dict) or set(row) not in ({"target", "source"}, {"target", "value"}):
            raise invalid()
        target = row["target"]
        if not isinstance(target, str) or not KEY.fullmatch(target) or target in seen:
            raise invalid()
        if "source" in row:
            source = row["source"]
            if not isinstance(source, str) or not (source in SOURCES or (source.startswith("params.") and source[7:] in params) or (source.startswith("object.") and KEY.fullmatch(source[7:]))):
                raise invalid()
        else:
            value = row["value"]
            if isinstance(value, str):
                if len(value) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise invalid()
            elif value is not None and type(value) not in (bool, int, float):
                raise invalid()
            elif type(value) is int and abs(value) > 2**53:
                raise invalid()
            elif type(value) is float and not math.isfinite(value):
                raise invalid()
        seen.add(target)
    for name, field in response.items():
        if not isinstance(field, str) or len(field) > 64 or (name in {"status", "receiptId"} and not KEY.fullmatch(field)) or (name in {"reason", "code"} and field and not KEY.fullmatch(field)) or (name in {"success", "failed", "unknown"} and (not field or any(ord(c) < 32 or ord(c) == 127 for c in field))):
            raise invalid()
    if len({response["success"], response["failed"], response["unknown"]}) != 3:
        raise invalid()

def request_body(mapping, payload, object_values=None):
    if mapping is None:
        return payload
    result = {}
    for row in mapping["request"]:
        if "value" in row:
            result[row["target"]] = row["value"]
            continue
        source = row["source"]
        if source.startswith("params."):
            name = source[7:]
            if name not in payload["params"]:
                raise invalid()
            result[row["target"]] = payload["params"][name]
        elif source.startswith("object."):
            if object_values is None or source[7:] not in object_values:
                raise invalid()
            result[row["target"]] = object_values[source[7:]]
        else:
            result[row["target"]] = payload[source]
    return result

def response_body(mapping, body):
    if mapping is None:
        return body
    if not isinstance(body, dict):
        raise ValueError("response must be an object")
    cfg = mapping["response"]
    status = body.get(cfg["status"])
    if not isinstance(status, str):
        raise ValueError("invalid status")
    statuses = {cfg["success"]: "SUCCESS", cfg["failed"]: "FAILED", cfg["unknown"]: "UNKNOWN"}
    if status not in statuses:
        raise ValueError("unknown status")
    return {"status": statuses[status], "receiptId": body.get(cfg["receiptId"]), "reason": body.get(cfg["reason"]) if cfg["reason"] else None, "code": body.get(cfg["code"]) if cfg["code"] else None}
