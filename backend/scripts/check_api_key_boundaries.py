"""Optional pure boundary self-check; no database, HTTP or credential issuance.

Not run during this delivery because the user prohibited backend tests.
"""

from pydantic import ValidationError

from app.common.auth import CurrentUser
from app.core.config import api_prefix
from app.modules.admin.api_keys import KeyCreateIn, metadata_permissions, token_page


def main():
    prefix = api_prefix("ontology")
    assert token_page("GET", prefix + "/metrics") == "metrics"
    assert token_page("GET", prefix + "/metrics/{id}") is None
    assert token_page("POST", prefix + "/metrics/{id}:trial") is None
    assert token_page("GET", api_prefix("admin") + "/api-keys") is None
    assert token_page("GET", prefix + "/dimensions/{id}/values") is None
    assert token_page("GET", prefix + "/object-types/{id}/usage") is None
    assert metadata_permissions(
        CurrentUser(id="u", name="n", func={"metrics": "manage", "users": "manage"})
    ) == {"metrics": "view"}
    assert metadata_permissions(CurrentUser(id="u", name="n", is_ontology_admin=True)) == {}
    body = dict(
        name="metadata-reader",
        env="测试",
        scopes=["元数据读取"],
        binding={"kind": "用户", "userId": "u"},
        ipAllow=["10.0.0.1/24"],
    )
    assert KeyCreateIn.model_validate(body).ip_allow == ["10.0.0.0/24"]
    for patch in (
        {"scopes": ["动作执行"]},
        {"binding": {"kind": "角色", "role": "admin"}},
        {"ipAllow": ["invalid"]},
        {"lifeDays": -1},
    ):
        try:
            KeyCreateIn.model_validate({**body, **patch})
        except ValidationError:
            pass
        else:
            raise AssertionError("Invalid credential configuration was accepted")


if __name__ == "__main__":
    main()
