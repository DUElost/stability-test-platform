# pr-automerge-queue 重试循环去掉末次失败后的尾随 sleep

Status: implemented
Class: bug-fix

## Decision

`scripts/ci/pr-automerge-queue.sh` 的 `head_merge_method` 重试循环：`sleep 5`
改为只落在重试之间（`[ "$attempt" -eq 3 ] || sleep 5`）——第 3 次（末次）
失败后直接告警回退，不再空转 5 秒（#851：队首 PR 的合入路径被无条件拖慢
5 秒，且该等待无任何作用对象）。

## Alternatives

- **动态退避（1s/2s/4s）**：过度设计——此处重试吸收的是 gh API 偶发抖动，
  固定 5s 已够，问题只在末次白等；
- **删除重试**：否决——GraphQL 偶发失败是真实存在的（当日平台窗口即为
  佐证），回退路径（幂等 enable）依赖三次尝试后的告警可观测。

## Verification

- `bash -n` 语法通过（shellcheck 本机不可用）；
- 逻辑沙盘（set -euo pipefail 同环境模拟三次失败）：3 次尝试后直达
  fallback，无尾随等待；`[ ... ] || sleep` 的短路失败在 set -e 下安全
  （失败侧非列表末命令，不触发退出）；
- 语义边界：attempt=3 时测试为真 → 跳过 sleep、列表 exit 0，不影响
  循环正常路径。

## Revisit

- 无——单点时序修正；若未来重试参数化（次数/间隔可配），随批次重审。
