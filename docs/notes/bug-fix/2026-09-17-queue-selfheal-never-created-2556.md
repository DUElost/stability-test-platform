# 队列队首：checks「从未创建」与「跑了没上报」分离，前者放一次带冷却的自续重基（#2556）

Status: implemented
Class: bug-fix

- 日期：2026-09-17

## Decision

`scripts/ci/pr-automerge-queue.sh` 把「required check 没上报」（`missing`）再分两类，
**只有「从未创建」才动一次手**：

| 形态 | 判据 | 动作 |
|---|---|---|
| 从未创建 | 该 head sha 上 `ci.yml` 的 run 数 == 0 | 执行**一次** `update_branch_tolerant`（base-change push 重新触发 `pull_request`），告警文案改为「已自续重基一次」 |
| 跑了没上报 | run 数 > 0 | 不动分支；告警「该 sha 上 CI 已触发过，但此 check 从未上报（疑 workflow 被禁用/改名/卡审批）」→ 人工 |
| 判据未知（探测失败/无 head sha） | 探测非零退出 | 不动分支；告警如实写「未能探明」→ 人工 |
| 判为红（`FAILURE`/`CANCELLED`/… 有结论） | 原有分类 | 不动分支（不变） |

判据数据源是 `gh api repos/<repo>/actions/workflows/ci.yml/runs?head_sha=<sha>
--jq '.total_count // 0'`，只在**已越过启动窗口且确有条目缺失**时才查——不是每轮
reconcile 的固定开销。探测输出非纯数字一律按「未知」处理，不猜。

读取用 `ALERT_TOKEN`（workflow 注入的 `GITHUB_TOKEN`，job 已授 `actions: write`）
而不是默认的 `GH_TOKEN`：默认那个可能是 `AUTO_MERGE_PAT`，其 scope 集不受本仓
控制（#1783 已证它缺 `workflow` scope）。若判据建立在它身上，PAT 一旦没有 Actions
读权限，自愈就会静默退化成「每轮交给人工」——能力等于没上。取舍同 #1246
（告警不依赖 PAT 是否含 Issues 权限）。

**冷却键是 `(队首 PR, head_sha)`，不是 PR**：标记
`<!-- queue-selfheal: head=#N sha=<sha> -->` 写进 `ci/queue-blocked` 告警正文（与
去重指纹同处，不新增状态存储——reconcile 是无状态 job，正文是现成的跨轮持久面）。
同一 sha 第二次仍无 run → 不重复动手，措辞升级为「已试过自续重基一次仍无 run →
需人工」；换了 head sha（自愈后的新提交）即一次全新判定。指纹同时带上自愈状态与
sha 前缀，否则「第一次」与「冷却用尽」两种正文会撞成同一指纹而被去重掉。

另加重入护栏：**没落后 main 时不做无意义的 base-change**（`behind_by == 0` 时
`gh pr update-branch` 会硬失败并染红 reconcile），直接在告警里交回人工；有 required
check 仍在跑（`pending`）时也不自愈，避免打断在途 run。

涉及文件：`scripts/ci/pr-automerge-queue.sh`、`tests/test_automerge_queue_alerts.py`、
`docs/development/repository-workflow.md`（队首停摆处置一节）。

## Alternatives

- **保留原样（`missing` 与失败同归「红队首」不重基）**：代价已实测——#2529 一次
  GitHub 触发丢失 = 停摆 63 分钟、其后 14 个 PR 排队，且告警给的处置清单（修 check /
  解冲突 / 让位）**三条都不适用**：没有 run，就没有可修的 check。否决。
- **提高宽限阈值 / 等更久再告警**：这是把停摆改名成抑制，不解决「分类粒度」这个本质
  问题（#2556 原文「不要用等更久来修」）。否决。
- **无冷却地自愈（missing 就重基）**：`missing ↔ 重基` 死循环——每轮 reconcile 推一次
  提交烧全量 CI。否决；冷却键选 `(PR, sha)` 是因为自愈成功后 sha 必变（base change 是
  一个新提交），新 sha 该有全新判定，而同一 sha 的重复动作纯属空转。
- **冷却状态另立存储（标签 / 独立 issue / 缓存）**：告警正文已经承载指纹，且
  `ci/queue-blocked` 本就是这条链路的可见性通道；多一个存储面就多一处不一致（告警被
  人工关闭时冷却记录的去留要另行定义）。否决。
- **用 rollup 里的 `FAILURE/CANCELLED/STALE` 当判据**：那三种都**有 run 记录**，正是
  「跑了但结果不满足」的形态，与「GitHub 压根没创建 run」是两回事（#2556 原文已钉死）。
  否决。
- **把 `queue_head_telemetry.py` 一并改成同一判据**：它是按需诊断工具、不参与自动化
  动作，且其 `MISSING`（status 缺失）目前归 `REQUIRED_CHECK_PENDING`。同 PR 改它会扩大
  面；记入 Revisit。
- **判据读取复用默认 `GH_TOKEN`**：那可能是 `AUTO_MERGE_PAT`，其 scope 集不受本仓控制；
  若它无 Actions 读权限，判据恒为「未知」→ 每次停摆都退回人工，本单的能力静默消失。
  改用 job 已授 `actions: write` 的 GITHUB_TOKEN。否决默认令牌。
- **顺便修「rollup 完全为空 → 启动窗口判据恒为真、永不告警」**：#1792 的既定设计
  （无 `startedAt` 视为极早期），本单不动。见 Revisit。

## Verification

- `bash -n scripts/ci/pr-automerge-queue.sh` → 通过。
- `python -m pytest tests/test_automerge_queue_alerts.py -q` → **27 passed**（原 20 +
  新 7）。
- 新场景（每条都有反向对照，夹具是「假 `gh` 跑真实脚本」）：
  - `test_never_created_checks_self_heal_with_one_branch_update`：run 数 0 + behind →
    调用 `pr update-branch`，告警正文含「已自续重基一次」与 sha 标记，且**不含**「不对
    红队首自动重基」；
  - `test_second_reconcile_on_same_sha_does_not_self_heal_again`：把上一轮脚本自己写出
    的正文喂回 → **不**再 update-branch，`issue edit` 升级为「已试过自续重基一次仍无
    run」；
  - `test_exhausted_self_heal_alert_is_deduped`：冷却用尽后重复 reconcile → 零写入；
  - `test_self_heal_cooldown_is_keyed_by_head_sha`：正文里是**另一个** sha 的标记 →
    仍允许自愈（冷却不得退化成 PR 级）；
  - `test_never_created_without_base_change_escalates_without_push`：behind=0 →
    不调用 update-branch，告警写「未落后 main」；
  - `test_ci_run_probe_failure_does_not_self_heal`：探测失败 → 不重基、仍告警；
  - `test_ci_run_probe_uses_the_actions_capable_token`：判据读取带的是
    `ALERT_TOKEN`（夹具按调用记录 `GH_TOKEN`）；
  - `test_missing_check_entry_still_opens_alert`（既有，已按新判据补 run 数=3 与 head
    sha）：**跑了没上报**不重基——这是判据另一半的反向钉子。
- **反向验证**（逐条把守卫改回宽松形态，对应用例必须变红，实测均红且只有目标用例红）：
  1. 冷却门 `if self_heal_already_attempted …` → `if false` ⇒
     `test_second_reconcile_on_same_sha_does_not_self_heal_again` 红；
  2. run 数判据 `if [ "$ci_runs" != "0" ]` → `if false` ⇒
     `test_missing_check_entry_still_opens_alert` 红；
  3. behind 护栏 `if [ "$behind" -eq 0 ]` → `if false` ⇒
     `test_never_created_without_base_change_escalates_without_push` 红；
  4. 冷却键收起 sha（grep 只匹配 `sha=`）⇒
     `test_self_heal_cooldown_is_keyed_by_head_sha` 红；
  5. 判据读取改回默认 `GH_TOKEN`（`probe_gh` → `gh`）⇒
     `test_ci_run_probe_uses_the_actions_capable_token` 红。
- 既有 20 例（#1246 红队首告警、#1761 pending 不告警、#1792 启动窗口与 NEUTRAL 放行、
  #1783 workflow scope、告警创建失败不染红）全部继续通过——**红队首不重基**与
  **全绿 + behind 照常重基**两条既有语义未退化。

## Revisit

- **「rollup 完全为空 → 启动窗口恒为真」的静默停摆**：判据用的是 rollup 里最早的
  `startedAt`，一条 check 都没有时（#2556 的纯形态：`ci.yml` 没触发、也没有 Vercel/
  Cursor 之类的旁路 check-suite）它永远是「极早期」→ 既不告警也不自愈。本次事件里
  是旁路 check-suite 提供了时间戳才走到判据上。**这是 #1792 既定设计与真实停电之间的
  缺口，本单未动**；若再现「队首长时间零 check 且无告警」，升级方向是用 head commit
  的提交时刻（`repos/<repo>/commits/<sha>`）替代 rollup `startedAt` 当窗口基准。
- **自愈一次是否足够**：选「至多一次」是为了杜绝死循环；若将来出现「一次重基不够、
  需要第二次才能触发」的实证（例如 GitHub 侧持续丢触发），应改为「每个新 sha 至多一次
  + 全局次数上限」，而不是简单取消冷却。
- **`update_branch_tolerant` 硬失败时无冷却记录**：push 若以未识别错误失败，脚本按既有
  语义让 reconcile 红退，此时告警（及其 sha 标记）尚未写出 → 下一轮会再试一次。这是
  有意的（错误≠空转），但若成为噪声源，应改为「先记标记再 push」。
- **`queue_head_telemetry.py` 未同步**：它仍把 status 缺失的 required check 归入
  `REQUIRED_CHECK_PENDING`（口径为「机器所有、等待」），与脚本新判据（可能是 GitHub
  触发丢失）不一致。按需诊断工具影响有限；若再现同类停摆，应让它直接显示
  `ci.yml` 在该 sha 上的 run 数。#1792 的 note 里已记过「三份口径漂移」，本单是第 4 处
  同源判据——**收敛方向仍是让告警脚本消费单一判据源**。
- **本单只放开队首**：非队首 PR 的同类形态不处理（它们本来就该等队首），不扩面。
