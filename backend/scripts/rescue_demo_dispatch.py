import asyncio
import json
import os
import socket
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
from sqlalchemy import select
from app.jobs.scope import scoped_sessions
from app.modules.dynamic.models import ActionRun
from app.modules.integration.execution import QUEUED, dispatch_one
from app.modules.integration.models import CallKind, ExtSystem, WritebackCall


async def main():
    targets = json.loads((Path(__file__).resolve().parents[1] / '.local-rescue' / 'worker-targets.json').read_text(encoding='utf-8'))
    while True:
        for target in targets:
            oid = target['ontologyId']
            async with scoped_sessions(oid)() as session:
                ids = list(await session.scalars(
                    select(WritebackCall.id).join(ActionRun, WritebackCall.action_run_id == ActionRun.id)
                    .join(ExtSystem, WritebackCall.sys_id == ExtSystem.id)
                    .where(WritebackCall.kind == CallKind.write, WritebackCall.msg == QUEUED,
                           ActionRun.action_type_code == target['actionType'], ExtSystem.code == target['systemCode'],
                           ExtSystem.local_only.is_(True), ExtSystem.endpoint == target['origin'])
                    .order_by(WritebackCall.id).limit(10)))
            for call_id in ids:
                await dispatch_one(call_id, oid)
        await asyncio.sleep(3)


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
