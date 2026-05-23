#!/usr/bin/env bash
# stability-test-platform — EOL 清地雷脚本
#
# 用途:把工作目录中"应该是 LF"的文本文件统一收敛到 LF,清除
# 历史上 PlanEditPage.tsx 等文件遗留的混合 EOL / 多重 CRLF 噪声。
#
# 用法:
#   tools/dev/normalize-eol.sh                  # 全仓扫描修复(干运行,先看 plan)
#   tools/dev/normalize-eol.sh --apply          # 实际执行
#   tools/dev/normalize-eol.sh <file>           # 单文件修复(单文件默认 --apply)
#   tools/dev/normalize-eol.sh --check          # 仅检测,有混合 EOL 返回非零
#
# 设计:
#   1. 全仓模式:复用 git ls-files --eol 输出,只挑出 attr=eol=lf 但
#      工作目录 w/crlf 或 w/mixed 的文本文件,逐个用 git 自身规范化
#      (git add --renormalize)
#   2. 单文件模式:直接 dos2unix / sed 替换,再 git add --renormalize
#   3. --check 模式:遇到 mixed 文件就退出非零,用于 CI
#
# 不会 commit,所有改动留在 stage 区,人工确认后再提交。

set -e

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$ROOT" ]; then
  echo "error: not inside a git repository" >&2
  exit 2
fi
cd "$ROOT"

MODE="dryrun"
TARGET=""
INCLUDE_CRLF="false"   # 默认只清 w/mixed(真地雷);w/crlf 是 Windows checkout 正常状态

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)      MODE="apply"; shift ;;
    --check)      MODE="check"; shift ;;
    --all-crlf)   INCLUDE_CRLF="true"; shift ;;
    -h|--help)
      cat <<'USAGE'
usage: tools/dev/normalize-eol.sh [--apply|--check] [--all-crlf] [<file>]

模式:
  (无参数)         干运行,列出工作目录中 EOL 已漂移成 mixed 的文件
  --apply         实际规范化(git add --renormalize),改动留在 stage
  --check         CI 模式,有 mixed 文件即退出非零
  --all-crlf      把 w/crlf 也算地雷(默认只算 w/mixed)。Windows checkout
                  下大量文件本来就是 CRLF,加这个会激进地把它们全统一到 LF
  <file>          单文件修复,默认 --apply

环境:
  .gitattributes 已设 * text=auto eol=lf,入库统一 LF;Windows 工作目录
  允许 CRLF。本脚本核心目标是清除 w/mixed(同一文件 LF 与 CRLF 混杂)
  这种由 IDE/插件错误处理 \r\n 引发的真污染。
USAGE
      exit 0
      ;;
    -*)
      echo "error: unknown option $1" >&2
      echo "usage: $0 [--apply|--check] [--all-crlf] [<file>]" >&2
      exit 2
      ;;
    *)
      MODE="single"
      TARGET="$1"
      shift
      ;;
  esac
done

if [ "$MODE" = "single" ]; then
  if [ ! -f "$TARGET" ]; then
    echo "error: $TARGET not found" >&2
    exit 2
  fi
  echo "Normalizing $TARGET ..."
  # 去 CR(把 CRLF / CR-only 都收敛到 LF)
  # sed -i 在 Git Bash for Windows 上是 GNU sed,支持 -i 无后缀写法
  sed -i 's/\r$//' "$TARGET"
  git add --renormalize "$TARGET"
  echo "Done. Staged: $TARGET"
  exit 0
fi

# 找到所有"EOL 已漂移"的文件。
# 默认只算 w/mixed(真地雷:同一文件混着 LF 与 CRLF,IDE/插件污染征兆);
# --all-crlf 时把 w/crlf 也并入(Windows checkout 正常状态,加这个是激进收敛)。
# git ls-files --eol 输出格式: <metadata 空格分隔>\t<path>(只有一个 tab)
if [ "$INCLUDE_CRLF" = "true" ]; then
  pattern='w/(crlf|mixed)'
  label="mixed|crlf"
else
  pattern='w/mixed'
  label="mixed"
fi

candidates=$(git ls-files --eol \
  | awk -F'\t' -v pat="$pattern" '
      {
        meta = $1
        path = $2
        if (meta ~ pat && meta ~ /eol=lf/) {
          print path
        }
      }
    ')

if [ -z "$candidates" ]; then
  echo "OK: no EOL drift detected (scope: $label)."
  exit 0
fi

count=$(printf '%s\n' "$candidates" | wc -l)
echo "Found $count file(s) with EOL drift (worktree matches $label, attr=eol=lf):"
printf '%s\n' "$candidates" | sed 's/^/  - /'

case "$MODE" in
  check)
    echo
    echo "FAIL (--check): mixed-EOL files exist."
    exit 1
    ;;
  dryrun)
    echo
    echo "Dry run. Re-run with --apply to normalize."
    exit 0
    ;;
  apply)
    echo
    echo "Applying normalization via 'git add --renormalize' ..."
    # 一次性 renormalize,git 自动按 .gitattributes 处理 EOL
    # 注意:这只规范化 stage 区;工作目录文件 OS 默认可能再次被检出为 CRLF,
    # 这是正常行为(.gitattributes 设了 text=auto eol=lf 但 Windows 仍允许
    # CRLF 在工作目录)。只要入仓是 LF,后续 diff 就不会再有 EOL 噪声。
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      git add --renormalize -- "$f"
    done <<< "$candidates"
    echo "Done. Run 'git diff --cached --stat' to inspect, then commit."
    exit 0
    ;;
esac
