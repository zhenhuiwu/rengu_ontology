#!/usr/bin/env bash
# 每人一个 git worktree + 自己的分支 + 自己的开发端口；master 是集成主干（http://localhost:5280）。
# 用法：
#   scripts/wt.sh new <名字>      建自己的工作区并启动开发服务器
#   scripts/wt.sh sync            把 master 的最新改动同步到自己分支（在自己工作区里执行）
#   scripts/wt.sh publish         检查通过后合入 master（在自己工作区里执行）
#   scripts/wt.sh dev             重新启动自己的开发服务器
set -euo pipefail
MAIN=/workspace/ui-vue
WT=/workspace/ui-vue-wt
declare -A PORT=([pde]=5286 [static]=5282 [dynamic]=5283 [qa]=5284 [palantir]=5285)
cmd=${1:-}; name=${2:-}
cur_branch() { git rev-parse --abbrev-ref HEAD; }
start_dev() {
  local n=$1 dir=$WT/$1 p=${PORT[$1]}
  pkill -f "vite --host 0.0.0.0 --port $p" 2>/dev/null || true
  (cd "$dir" && nohup npx vite --host 0.0.0.0 --port "$p" --strictPort > "/tmp/vite-$n.log" 2>&1 &)
  echo "开发服务器：http://localhost:$p  （截图：SHOT_BASE=http://localhost:$p npm run shot -- <路由>）"
}
case "$cmd" in
  new)
    [[ -n "$name" && -n "${PORT[$name]:-}" ]] || { echo "名字只能是：${!PORT[*]}"; exit 1; }
    mkdir -p "$WT"
    if [[ ! -d "$WT/$name" ]]; then
      if git -C "$MAIN" show-ref --quiet "refs/heads/$name"; then git -C "$MAIN" worktree add "$WT/$name" "$name"
      else git -C "$MAIN" worktree add -b "$name" "$WT/$name" master; fi
      ln -sfn "$MAIN/node_modules" "$WT/$name/node_modules"
    fi
    echo "工作区：$WT/$name （分支 $name）"; start_dev "$name" ;;
  sync)
    b=$(cur_branch); [[ "$b" != master ]] || { echo "请在自己的工作区里执行"; exit 1; }
    git diff --quiet && git diff --cached --quiet || { echo "先提交自己的改动再同步"; exit 1; }
    git rebase master ;;
  publish)
    b=$(cur_branch); [[ "$b" != master ]] || { echo "请在自己的工作区里执行"; exit 1; }
    git diff --quiet && git diff --cached --quiet || { echo "先提交自己的改动再合入"; exit 1; }
    git rebase master
    python3 scripts/check_imports.py
    npx vite build --logLevel error --outDir /tmp/wt-build-$b >/dev/null || { echo "构建失败，没有合入"; exit 1; }
    git -C "$MAIN" merge --ff-only "$b" && echo "已合入 master，http://localhost:5280 会自动刷新" ;;
  dev)
    b=$(cur_branch); start_dev "$b" ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
