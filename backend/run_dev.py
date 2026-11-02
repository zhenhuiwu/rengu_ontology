"""Portable API launcher; the event worker requires an explicit, bounded selection."""

import argparse
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def commands(arguments) -> list[list[str]]:
    api = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", arguments.host, "--port", str(arguments.port),
    ]
    env_file = BACKEND / ".env"
    if env_file.is_file():
        api.extend(["--env-file", str(env_file)])
    result = [api]
    if arguments.with_worker:
        result.append([
            sys.executable, "-m", "app.modules.event.worker", "--ontology", str(arguments.ontology),
            "--event-type", arguments.event_type, "--subscriber", arguments.subscriber,
            "--after-id", str(arguments.after_id),
        ])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="默认仅启动API；显式选择单订阅、新事件高水位后才联启事件worker。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--api-only", action="store_true", help="只启动API（默认）")
    mode.add_argument("--with-worker", action="store_true", help="同时启动限定范围的事件worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ontology", type=int)
    parser.add_argument("--event-type")
    parser.add_argument("--subscriber")
    parser.add_argument("--after-id", type=int)
    arguments = parser.parse_args()
    if not 1 <= arguments.port <= 65535:
        parser.error("--port 须为1至65535")
    selection = (arguments.ontology, arguments.event_type, arguments.subscriber, arguments.after_id)
    if arguments.with_worker:
        if (
            not arguments.ontology or arguments.ontology < 1
            or not arguments.event_type or not arguments.subscriber
            or arguments.after_id is None or arguments.after_id < 0
        ):
            parser.error(
                "--with-worker 必须指定 --ontology、--event-type、--subscriber、--after-id"
                "（上线前只读取得的事件主键高水位）"
            )
    elif any(value is not None for value in selection):
        parser.error("所选事件参数仅可与 --with-worker 一起使用")
    children = []
    try:
        print("启动API及限定事件worker" if arguments.with_worker else "仅启动API；事件worker未启用", flush=True)
        for command in commands(arguments):
            children.append(subprocess.Popen(command, cwd=BACKEND))
        while all(child.poll() is None for child in children):
            time.sleep(0.25)
        return next((child.returncode for child in children if child.returncode), 0)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
