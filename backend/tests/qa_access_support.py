"""QA业务API测试的真实静态对象身份与角色授权；不覆盖行级过滤器。"""

from app.modules.static.sections import BUILDERS
from app.modules.versioning.sections import isolated_sections
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles


async def seed_qa_access(session):
    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["versioning", "static", "biz", "qa"])
    await grant_fixture_roles(session, {"U010001": "R-01", "U010763": "R-05"})
