"""Run the explicitly scoped local demonstration event consumer."""
import asyncio
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
from app.modules.event.worker import run_selected

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    targets = json.loads((Path(__file__).resolve().parents[1] / ".local-rescue" / "worker-targets.json").read_text(encoding="utf-8"))
    async def main():
        await asyncio.gather(*(run_selected(t["ontologyId"], t["eventType"], t["subscriber"], t["afterId"]) for t in targets))
    asyncio.run(main())
