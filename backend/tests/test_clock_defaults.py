from datetime import datetime

from app.core.config import DEV_FIXED_NOW, Settings


def test_real_clock_default_and_explicit_replay():
    for env in ("dev", "prod"):
        assert Settings(env=env, fixed_now=None, _env_file=None).effective_fixed_now is None
    for env in ("test",):
        assert Settings(env=env, fixed_now=None, _env_file=None).effective_fixed_now == DEV_FIXED_NOW
    replay = datetime.fromisoformat("2026-09-01T00:00:00+08:00")
    assert Settings(env="dev", fixed_now=replay, _env_file=None).effective_fixed_now == replay
