#!/usr/bin/env python3
"""Compatibility entry: use the same cross-platform Node runner as npm run shot."""
import pathlib
import shutil
import subprocess
import sys

if __name__ == "__main__":
    node = shutil.which("node")
    if node is None:
        print("缺少Node.js，请使用安装了项目Node依赖的运行环境。", file=sys.stderr)
        sys.exit(2)
    sys.exit(subprocess.call([node, str(pathlib.Path(__file__).with_suffix(".mjs")), *sys.argv[1:]]))
