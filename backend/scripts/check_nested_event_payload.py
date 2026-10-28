"""Small manual check for nested event payload validation; run from backend when needed."""
from types import SimpleNamespace
from app.core.errors import AppError
from app.modules.event.ingress import validate_fields
from app.modules.event.payload_schema import validate_definition

schema = {"fields": [{"name": "队伍", "api": "teams", "type": "数组", "required": True, "schema": {"item": {"type": "对象", "schema": {"fields": [{"name": "编号", "api": "id", "type": "字符串", "required": True, "schema": None}]}}}}]}
field = SimpleNamespace(api="incident", type="对象", required=True, schema=schema)
validate_definition(field.type, schema)
validate_fields([field], {"incident": {"teams": [{"id": "T1"}]}})
for invalid in ({"incident": {"teams": [{}]}}, {"incident": {"teams": "T1"}}):
    try:
        validate_fields([field], invalid)
    except AppError:
        pass
    else:
        raise AssertionError("invalid nested payload accepted")
