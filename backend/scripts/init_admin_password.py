"""Initialize/reset the real admin password without printing secrets.

Run from backend: .venv/Scripts/python.exe scripts/init_admin_password.py [--reset]
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.db.models import Base  # noqa: F401 - register all referenced models
from app.db.session import dispose_engine, get_sessionmaker
from app.modules.admin.login import hash_password
from app.modules.admin.models import AuthCredential, AuthSession, SysUser, UserStatus

OUTPUT = Path(__file__).resolve().parents[1] / ".local" / "admin-login.txt"
DEFAULT_ADMIN_PASSWORD = "admin123"


async def main(reset: bool) -> None:
    output = OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with get_sessionmaker()() as session, session.begin():
            user = await session.scalar(select(SysUser).where(SysUser.code == "U010001").with_for_update())
            if user is None or user.account != "admin" or user.status != UserStatus.active:
                raise SystemExit("需要已启用的 U010001/admin 账号；未作修改。")
            credential = await session.get(AuthCredential, user.id)
            if credential is not None and not reset:
                raise SystemExit("密码已初始化；如需轮换，请显式使用 --reset（会撤销全部会话）。")
            if output.exists() and not reset:
                raise SystemExit("本地凭据文件已存在；未覆盖。请核实后使用 --reset。")
            password = DEFAULT_ADMIN_PASSWORD
            encoded = await asyncio.to_thread(hash_password, password)
            if credential is None:
                session.add(AuthCredential(user_id=user.id, password_hash=encoded))
            else:
                credential.password_hash = encoded
            await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            # Write before DB commit so an unwritable file cannot silently lose the new password.
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("仁谷科技管理员登录\n账号：admin\n密码：" + password + "\n")
        print("管理员密码已设置；全部旧会话已撤销。凭据仅写入 backend/.local/admin-login.txt。")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="恢复默认密码并撤销现有会话")
    args = parser.parse_args()
    asyncio.run(main(args.reset))
