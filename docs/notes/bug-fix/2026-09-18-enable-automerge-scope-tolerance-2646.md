# #2646 enable auto-merge 的 workflow-scope 拒绝容错（#1783 同根因的未覆盖调用点）

Status: implemented
Class: bug-fix

## Decision

`scripts/ci/pr-automerge-queue.sh` 三处改动：

1. **新增 `enable_auto_tolerant()`**：把队首启用 auto-merge 的那次 `gh pr merge --auto --merge`
   收进容错分支——识别 workflow-scope 拒绝后**绿退 + 人工动作指引**，并 `return 2`
   作为「绿退但队列被卡」的**可区分信号**；
2. **调用点按退出码三分**：`0` = 无碍；`2` = 凭据受阻（置 `head_enable_blocked`）；
   **其它 = 真故障，`exit` 上抛**；
3. **`alert_queue_blocked` 增 `reason_code`（第 7 参）**：进指纹 + 正文给出可执行动作。

## 缺陷确认（逐条回源码，与 issue 一致）

| issue 判据 | 复核 |
|---|---|
| `:303` `gh pr merge "$url" --auto --merge` 无任何容错 | ✅ 原为裸调用（同段 `:307` 的 `--disable-auto` 有 `\|\| true`） |
| `set -euo pipefail` 使整 job 红 | ✅ 脚本第 3 行 |
| 队首无 auto-merge → 跳过 head update | ✅ `if [ -z "$auto_method" ]; then … skip head update; exit 0` |
| #1783 修的是同一根因的 `update-branch` 路径 | ✅ `update_branch_tolerant()` 内已有 `without .?workflow.? scope` 分支 |

**自持停摆链**（issue 描述，代码证实）：enable 失败 → `set -e` 终止 → 队首永远拿不到
auto-merge → 下游「队首无 auto-merge 就跳过 head update」→ **停摆被固定**、不自愈。
实测整队列零合入 **6h20m**（23 个 open PR）。

## 两处**我自己引入又修掉**的缺陷（留痕）

### 1. `cmd || var=...` 会吞掉**所有**非零退出

初版我写 `enable_auto_tolerant "$num" "$url" || head_enable_blocked="$num"`。
`||` 右侧赋值成功 ⇒ **整行退出码为 0** ⇒ 真故障（如 HTTP 500）被伪装成绿。

**这不是理论问题**：我写的负向对照用例
`test_enable_automerge_unrelated_failure_still_fails_loudly` **当场红了**，报
「非 workflow-scope 的 enable 失败必须上抛，实际 rc=0」。这正是**先写负向对照**的价值——
若只写正向用例，这个吞异常的实现会顺利合入。

改为按退出码三分（见 Decision #2），负向对照随即通过。

### 2. 我的测试 fixture 自相矛盾

初版用例复用 `_head_detail()`，而它预置 `autoMergeRequest.mergeMethod = "MERGE"`——
**那是「已挂 auto-merge」的形态**，而脚本只有在 `method != MERGE` 时才走 enable 分支。
fixture 既说「已挂」又期望走 enable，使「兜底是否误吞真故障」的断言失去意义。

新增 `_head_detail_no_auto()`（把 `autoMergeRequest` 置 `None`）修正。

> 教训：构造用例时必须让 fixture 满足**被测分支的进入条件**；否则用例看似在测该分支，
> 实则测的是别的路径。

## Alternatives

- **只加 `|| true`（与 `--disable-auto` 同形）** → 否决：会把真故障一并吞掉，
  且**不发告警**，运维只看到「队列不动、日志无异常」——正是本单要消除的形态。
- **只绿退、不加 `reason_code`** → 否决：既有指纹 `head=#N failed=<失败项>` 对本状态
  **恒为空**（队首 required checks 全绿），会与「无失败项的普通停摆」共用指纹、
  正文互相覆盖，运维无法稳定区分处置（一个要补 scope、一个要排查 CI）。
- **补 `workflow` scope 凭据（issue 的路径 B）** → 否决：该凭据因此可**改 CI 定义**，
  属**安全面扩张**，必须 owner 裁决；issue 的验收标准已明示此点。本 PR 只做无害化。
- **匹配具体报错文案判定「已被并发启用」** → 否决：报错形态不稳定（与
  `update_branch_tolerant` 末尾「按 PR 状态判定而非匹配文案」同一取向），改为复读
  `autoMergeRequest` 的**结果**判定。

## Verification

- `./scripts/run_pytest.sh tests/test_automerge_queue_alerts.py -q` → **30 passed**
  （既有 27 + 新增 3）；
- **红绿双向**：还原为裸调用 → 新增的 2 个容错/告警用例**红灯**；
  负向对照两种情况下都通过（它断言的是「失败须上抛」，修复前亦成立）——符合预期；
- **相关套件**：`tests/ -k "queue or automerge or gate"` → **110 passed**；
- `ruff` 通过；`check_governance_surface.py --check` → S1–S14、S5x 全绿；
- `bash -n` 语法校验通过（无 shellcheck 门禁）。

## Revisit

- **`reason_code` 目前只有一个取值**（`credential-scope`）。#2624 建议的
  `reason_code` 消费面（队首停摆告警只看 checks 不看 mergeable）可**复用本参数**扩展
  （如 `conflicting` / `unreported-check`），无需再动指纹结构——本单已把该扩展点建好。
- **`exit "$enable_rc"` 的语义**：真故障仍让 job 红（与修复前一致）。这是有意的——
  本单只收「凭据配置」这一**可处置**状态。若将来希望真故障也走告警而非红 job，
  应显式评估（那会削弱 CI 的失败可见性）。
- **未验证真实 PAT 行为**：本单用注入式 mock 覆盖（与既有 #1783 用例同法）。
  触发条件：下次队首 PR 改 `.github/workflows/*` 时，应能从 issue 告警的
  `reason=credential-scope` 直接认出，而不再是「队列无故停摆」。
