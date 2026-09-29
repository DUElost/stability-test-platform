#!/usr/bin/env bash
# 只读盘点：分支 / worktree / 远端 / 本机产物。不做任何修改。
# 用法：bash .claude/skills/workspace-hygiene/scripts/hygiene-scan.sh
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

hr() { printf '\n=== %s ===\n' "$1"; }

hr "main 同步状态"
git status -sb | head -1
git log --oneline -1

hr "本地分支（unique = 相对 origin/main 的独有提交数；0 且 ancestor=YES 才可回收）"
while read -r br; do
  [ "$br" = "main" ] && continue
  u=$(git rev-list --count "origin/main..$br" 2>/dev/null || echo '?')
  if git merge-base --is-ancestor "$br" origin/main 2>/dev/null; then anc=YES; else anc=NO; fi
  inwt=$(git worktree list --porcelain | grep -c "refs/heads/$br$")
  printf '  %-46s unique=%-4s ancestor=%-4s in_worktree=%s\n' "$br" "$u" "$anc" "$inwt"
done < <(git branch --format='%(refname:short)')

hr "worktree（含 dirty 数与滞后提交数）"
git worktree list --porcelain | awk '/^worktree /{print $2}' | while read -r wt; do
  d=$(git -C "$wt" status --porcelain 2>/dev/null | wc -l)
  head=$(git -C "$wt" log --oneline -1 2>/dev/null)
  printf '  %-56s dirty=%-3s %s\n' "$wt" "$d" "$head"
done

hr "孤儿 worktree（gitdir 元数据失联，逃过 worktree list 与 remove）"
found=0
for base in .wt /tmp; do
  [ -d "$base" ] || continue
  while IFS= read -r dotgit; do
    dir=$(dirname "$dotgit")
    t=$(sed -n 's/^gitdir: //p' "$dotgit" 2>/dev/null)
    [ -n "$t" ] || continue
    if [ ! -e "$t" ]; then
      printf '  ORPHAN %s  (gitdir %s 已不存在)\n' "$dir" "$t"
      found=1
    fi
  done < <(find "$base" -maxdepth 3 -name .git -type f 2>/dev/null)
done
[ "$found" = 0 ] && echo "  (无)"

hr "远端分支"
git branch -r | sed 's/^/  /'

hr "stash / prune"
printf '  stash 条数: %s\n' "$(git stash list | wc -l)"
pr=$(git worktree prune --dry-run -v 2>&1)
[ -n "$pr" ] && printf '  prune 待清: %s\n' "$pr" || echo "  prune: 无待清条目"

hr "未跟踪文件（防 git add -A 误入库）"
git status --porcelain | grep '^??' | sed 's/^/  /' || true
[ -z "$(git status --porcelain | grep '^??')" ] && echo "  (无)"

hr "registry 概览"
if [ -f tools/dev/ai_work.py ]; then
  s=$(python3 tools/dev/ai_work.py status 2>/dev/null)
  printf '  CODING 总数:            %s\n' "$(printf '%s' "$s" | grep -c 'lifecycle=CODING')"
  printf '  CODING+MERGED 矛盾态:   %s   <- 这些可 finish 收口\n' \
    "$(printf '%s' "$s" | grep 'lifecycle=CODING' | grep -c 'integration=MERGED')"
  printf '  CODING+NO_PR 待人工核:  %s   <- 无 PR，不能批量收口\n' \
    "$(printf '%s' "$s" | grep 'lifecycle=CODING' | grep -c 'integration=NO_PR')"
else
  echo "  (未找到 tools/dev/ai_work.py)"
fi

hr "/tmp 概览"
printf '  条目数: %s   体积: %s\n' "$(ls -A /tmp 2>/dev/null | wc -l)" "$(du -sh /tmp 2>/dev/null | cut -f1)"
echo "  -- 日期分布 --"
(cd /tmp && ls -lAt --time-style=+'%Y-%m-%d' 2>/dev/null | awk 'NR>1 {print $6}' | sort | uniq -c | tail -10)
echo "  -- 体积 TOP10 --"
du -sh /tmp/* /tmp/.[!.]* 2>/dev/null | sort -rh | head -10 | sed 's/^/  /'

hr "/tmp 活挂载（AppImage fuse，删了会崩会话）"
m=$(mount 2>/dev/null | grep -iE 'ZCode|clash|\.mount_' | head -10)
[ -n "$m" ] && printf '%s\n' "$m" | sed 's/^/  /' || echo "  (无)"

hr "容器"
if command -v docker >/dev/null 2>&1; then
  docker ps -a --format '  {{.Names}} | {{.Status}} | project={{.Label "com.docker.compose.project"}}' 2>/dev/null \
    || echo "  (docker 不可用)"
  echo "  -- 僵尸栈线索：working_dir 宿主路径是否还在 --"
  for c in $(docker ps -a --format '{{.Names}}' 2>/dev/null); do
    wd=$(docker inspect "$c" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null)
    [ -n "$wd" ] || continue
    if [ -d "$wd" ]; then echo "  OK   $c  $wd"; else echo "  ZOMBIE $c  $wd (宿主源目录不存在)"; fi
  done
else
  echo "  (无 docker)"
fi

hr "磁盘"
df -h / | tail -1

printf '\n提示：本脚本只读。任何删除/回收前先按 SKILL.md 的判据归类并取得用户裁决。\n'
