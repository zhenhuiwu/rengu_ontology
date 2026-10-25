#!/usr/bin/env python3
"""检查 src 下所有 '@/...' 引用的文件都存在。缺文件就返回 1（pre-commit 和 wt.sh publish 都会调用）。"""
import re, sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
src = root / "src"
pat = re.compile(r"""['"]@/([^'"]+)['"]""")
missing = []
for f in list(src.rglob("*.ts")) + list(src.rglob("*.vue")):
    for m in pat.finditer(f.read_text(encoding="utf-8", errors="ignore")):
        p = src / m.group(1)
        cands = [p, p.with_suffix(p.suffix + ".ts") if p.suffix else p.with_suffix(".ts"),
                 p.with_suffix(".vue"), p / "index.ts", pathlib.Path(str(p) + ".ts")]
        if not any(c.is_file() for c in cands):
            missing.append(f"{f.relative_to(root)} -> @/{m.group(1)}")
if missing:
    print("以下引用的文件不存在（先建文件，再挂路由）：")
    print("\n".join(sorted(set(missing))))
    sys.exit(1)
print("引用检查通过")
