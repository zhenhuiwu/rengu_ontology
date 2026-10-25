#!/usr/bin/env python3
"""截图 + 验收。

用法:
  npm run shot -- <路由...>                  例：npm run shot -- / /build/ontology/object-types "/?drawer=metric:gross_margin"
  npm run shot -- --all                       截全部路由（首页 + 27 个二级页面）
  npm run shot -- "/admin/system/users#click=停用"   交互状态：打开页面后依次点击文字（多步用 > 分隔），再截图
  可选：--base http://localhost:5281（默认 http://localhost:5280，也可用环境变量 SHOT_BASE）
        --skip-check  只截图不验收

流程：每个路由先跑 /workspace/ui/check_onescreen.py（2560×1440、1920×1080 两档）和
/workspace/ui/check_fonts.py（字号 12/14/16/20/24、字重 400/600 白名单），打印结果；
再用 Playwright 以 viewport 1920×1080、deviceScaleFactor 1.3333 截出 2560×1440 的图到 shots/。
文件名 = 路由+状态 + __时分_短提交号，例如 use_ops_alerts__1642_e09a371.png；
写入前删掉同一路由+状态的旧图（含旧格式 use_ops_alerts.png），shots/ 里每个状态只留最新一张。
任一检查不通过，退出码为 1（图照样截，方便对照修改）。
"""
import os, re, subprocess, sys, pathlib, urllib.request, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOTS = ROOT / "shots"
CHECK_ONESCREEN = "/workspace/ui/check_onescreen.py"
CHECK_FONTS = "/workspace/ui/check_fonts.py"


def all_routes():
    src = (ROOT / "src" / "nav.ts").read_text(encoding="utf-8")
    return ["/"] + re.findall(r"path: '(/[^']+)'", src)


def slug(route: str) -> str:
    path, _, query = route.partition("?")
    s = (path.strip("/") or "home") + (("?" + query) if query else "")
    s = s.replace("/", "_").replace("?", "__").replace("&", "_").replace("=", "-").replace(":", "-")
    return re.sub(r"[^\w\-.]", "_", s)


def git_short() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip() or "nogit"
    except Exception:
        return "nogit"


def shot_path(route: str, stamp: str) -> pathlib.Path:
    """新文件名，并删除同一路由+状态的旧截图"""
    base = slug(route)
    old = re.compile(rf"^{re.escape(base)}(__\d{{4}}_[0-9a-z]+)?\.png$")
    for f in SHOTS.glob(f"{base}*.png"):
        if old.match(f.name):
            f.unlink()
    return SHOTS / f"{base}__{stamp}.png"


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def main(argv):
    base = os.environ.get("SHOT_BASE", "http://localhost:5280")
    skip = False
    routes = []
    it = iter(argv)
    for a in it:
        if a == "--base": base = next(it)
        elif a.startswith("--base="): base = a.split("=", 1)[1]
        elif a == "--skip-check": skip = True
        elif a == "--all": routes += all_routes()
        else: routes.append(a if a.startswith("/") else "/" + a)
    if not routes:
        print(__doc__); return 2
    base = base.rstrip("/")
    try:
        urllib.request.urlopen(base + "/", timeout=5)
    except Exception as e:
        print(f"无法访问 {base}：{e}\n请先 npm run dev（端口 5280）或 npm run build && npm run preview（端口 5281）。")
        return 2

    from playwright.sync_api import sync_playwright
    SHOTS.mkdir(exist_ok=True)
    stamp = f"{datetime.datetime.now():%H%M}_{git_short()}"
    summary = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1.3333, locale="zh-CN")
        for r in routes:
            path, _, clicks = r.partition("#click=")
            url = base + path
            print(f"\n=== {r} ===")
            ok1 = ok2 = True
            if not skip:
                c1, o1 = run([sys.executable, CHECK_ONESCREEN, url]); ok1 = c1 == 0
                print("[一屏检查]"); print("\n".join(l for l in o1.splitlines() if l.strip()))
                c2, o2 = run([sys.executable, CHECK_FONTS, url]); ok2 = c2 == 0
                print("[字号检查]"); print(o2)
            page = ctx.new_page()
            errors = []
            # console.error 和 [objectRegistry] 无法解析对象 的警告都算 FAIL
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" or (m.type == "warning" and m.text.startswith("[objectRegistry]")) else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(800)
            for text in filter(None, clicks.split(">")):
                try:
                    page.get_by_text(text, exact=True).locator("visible=true").first.click(timeout=5000)
                except Exception as e:
                    errors.append(f"点击“{text}”失败：{str(e).splitlines()[0]}")
                page.wait_for_timeout(600)
            out = shot_path(r, stamp)
            page.screenshot(path=str(out))
            page.close()
            if errors:
                print("[控制台错误] " + " | ".join(errors[:5]))
            print(f"[截图] {out}")
            summary.append((r, ok1, ok2, out, len(errors)))
        browser.close()

    print("\n=== 汇总 ===")
    bad = 0
    for r, ok1, ok2, out, ne in summary:
        flag = "SKIP" if skip else ("PASS" if ok1 and ok2 and ne == 0 else "FAIL")
        bad += flag == "FAIL"
        print(f"{flag}  {r:<48} 一屏:{'-' if skip else ('通过' if ok1 else '不通过')}  字号:{'-' if skip else ('通过' if ok2 else '不通过')}  控制台错误:{ne}  {out.relative_to(ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
