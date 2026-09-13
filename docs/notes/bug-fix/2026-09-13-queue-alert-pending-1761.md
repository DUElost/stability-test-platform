# #1761 修 ci/queue-blocked 把「进行中」误判为 missing

Status: implemented
Class: bug-fix

## Decision

改 `scripts/ci/pr-automerge-queue.sh` 的 required check 判定：由「只读 `.conclusion`」
改为**同时读 `.status`**，并做三分类：

| 形态 | 判定 | 处置 |
|---|---|---|
| `status == COMPLETED` 且 `conclusion == SUCCESS` | 通过 | 继续 |
| 无条目（`status` 落到 `MISSING`） | **真 missing** | **告警**（该 check 未注册/未上报，需人工排查） |
| `status != COMPLETED`（IN_PROGRESS/QUEUED/…） | **pending** | **不告警**，仅日志；且**不 resolve 存量告警** |
| `status == COMPLETED` 且 `conclusion != SUCCESS` | **failed** | **告警**（#1246 要覆盖的真实停摆） |

补 6 例回归测试（原 6 → 12）。

## 根因

原实现（`:259-270`）只取 `.conclusion`，而**检查进行中时该字段为空串** → 走
`// ""` → 打印 `${conclusion:-missing}` → **把「进行中」标成 `missing` 并触发告警**。
行内注释写「#1246：停摆不再只是日志一行」，设计意图是「红灯才告警」，实现却把
「还没跑完」一并纳入。

**实测规模**：2026-09-13 06:01–07:24（约 83 分钟）产生 **20 条**告警；
历史累计 **100 条** `ci/queue-blocked` issue，**99 条已自动 CLOSED**、仅 1 条 OPEN。
即告警几乎每个 PR 触发一次、全部瞬时误报——**通道饱和失效**，与 #1246 立此告警的
初衷（让真实停摆可见）相反。

**同仓库既有正确口径**：`tools/dev/queue_head_telemetry.py:147-161` 早已按
`status == "COMPLETED"` 区分 `failed` / `pending`，并给 pending 标
`REQUIRED_CHECK_PENDING` + `owner: machine` + `actionable: false`。实测同一时刻两工具
分歧：telemetry 判 #1745 为 pending（无需动作），告警脚本同时刻为它开出 #1760。
本单即让告警脚本与 telemetry 的口径对齐（**改判定，不改 telemetry**——其抬头明示
`reason_code` 是 advisory、任何 workflow 不得据其分支）。

## 一个必须保留的区分：MISSING ≠ pending

修复过程中发现（并已由测试钉住）：**无条目**（`status` 落到 `MISSING`）既不是在跑、
也不是「跑完失败」，而是该 check **根本没被注册/上报**——这是需人工排查的形态，
**必须继续告警**。首版实现把它误并入 pending 分支（`status != "COMPLETED"` 为真），
被 `test_missing_check_entry_still_opens_alert` 当场拦住。故三分类而非二分类。

## 兼容旧数据形态

GitHub 的 `statusCheckRollup` 条目一般带 `status`，但**不带 `status` 而有
`conclusion`** 意味着「已有定论」——按 `COMPLETED` 处理。否则会把失败误判为 pending
而**静默**，把本单的修复变成反向缺陷（漏报真实停摆）。此分支由既有 6 例测试
（其 fixture 恰好只造 `name`+`conclusion`）在首轮暴露并验证。

## Alternatives

- **仅把 pending 从告警集移除（二分类：failed vs pending）** → 否决：会把真 missing
  静默，漏报「check 未注册」这一需人工形态；见上「必须保留的区分」。
- **给 pending 加 `blocked_duration` 阈值（如 >15min 才告警）** → 否决（本单范围）：
  引入新阈值需独立裁决，且会把「CI 稍慢」变成新一类噪音；当前先消除**确定性误报**，
  阈值类增强留 Revisit。
- **pending 时也 resolve 存量告警** → 否决：CI 未跑完，真实失败可能紧随其后；此刻
  resolve 会造成「刚宣布恢复又立刻告警」的抖动。改为「既不新增也不 resolve」，
  由下一轮 reconcile 重新判定。
- **改 telemetry 去适配告警脚本** → 否决：telemetry 是新工具且分类正确，方向应是
  旧脚本向它对齐；且 telemetry 自带 `--self-test` 与本单的测试是两套独立保障。
- **在告警文案里区分 pending/failed 但仍告警** → 否决：文案区分不能减少 issue 数量，
  通道仍会被刷屏——问题在**告警动作**而非文案。

## Verification

- `python -m pytest tests/test_automerge_queue_alerts.py -q` → **12 passed**（原 6 + 新 6）；
- **红绿双向**：新测试在旧脚本上 → **3 failed**
  （`test_pending_check_does_not_open_alert` / `test_queued_check_does_not_open_alert`
  / `test_missing_check_entry_still_opens_alert`）；修复后 → **12 passed**；
- **jq 分类六形态实测**：`IN_PROGRESS`→pending、`QUEUED`→pending、
  `COMPLETED/SUCCESS`→通过、`COMPLETED/FAILURE`→failed、**无 `status` 有 `conclusion`**
  →COMPLETED/FAILURE、**无条目**→MISSING；
- **回归保护**：既有 6 例（红队首告警 / 同指纹零写入 / 指纹变化编辑 / 全绿关闭+update-branch
  / 空队列关闭 / 告警创建失败不阻断）全部保持通过——#1246 的真实停摆告警未被静音；
- 全量离线子集：`python -m pytest tests/ -q --ignore=...（两个容器文件）` → **252 passed**；
- `bash -n scripts/ci/pr-automerge-queue.sh` → 语法通过；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿。

## Revisit

- **`blocked_duration` 阈值告警**（pending 超过 N 分钟仍未完成）：本单刻意不做，
  需独立裁决——它能把「CI 真卡住」从噪音中分离，但也引入新阈值与新的误报面。
  可用 `queue_head_telemetry` 的 `blocked_duration` 字段作为数据源评估。
- **告警去重指纹**：当前按「队首 + 失败集」去重，每换队首即新指纹 → 每个 PR 一条
  issue。若真实停摆仍偏多，可考虑按**失败集**（跨队首）去重，让同一类故障收敛到一条。
  本单只消除误报，未改去重策略。
- **telemetry 与告警脚本的口径漂移**：二者现均按 `status == COMPLETED` 分类，但**代码
  仍有两份**（bash + python）。若出现第三次口径分歧，应考虑让告警脚本直接消费
  telemetry 的判定结果（需先解决 telemetry 抬头「不得据 reason_code 分支」的约束——
  该约束是针对 workflow 的，脚本内复用需独立裁决）。
