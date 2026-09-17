# 刷机失败率只读分诊：issue 实证窗口已过，5 条方向逐条落点（#1591）

Status: implemented
Class: bug-fix

> 本单**不改任何代码**：只读分诊 + 逐条结论，供 issue 收窄/关闭。证据全部来自生产库
> `SELECT`（DSN 仅经 `.env.backend`，未打印未落盘）。

## Decision

**issue 的实证窗口（2026-09-11/12）已经过去，当前链路上那些失败形态没有复现。**

近 14 天含 flash 步骤的 plan_run（`plan_run ⋈ job_instance ⋈ step_trace`）：

| run | 日期 | status | 失败步数 | 备注 |
|---|---|---|---|---|
| 365 / 366 / 369 / 372 / 373 | 09-11 ~ 09-12 | FAILED | 16 / 3 / 12 / 5 / 4 | **issue 的实证窗口** |
| 402（host 9.126） | 09-15 | SUCCESS | 0 | |
| 404（host 15.82） | 09-15 | FAILED | 1 | 见下（装机资源缺口，同日已补齐） |
| 405 / 407 / 413 / 415（host 15.82） | 09-15 ~ 09-16 | SUCCESS | 0 | 同一台 host 连绿 |

失败步骤累计（14 天）：`flash` 40 / `oobe` 17 / `flash_preflight` 1——**全部集中在 09-11/12
那批**；近三天的唯一失败是 run 404 的 `flash_preflight`。

run 404 的根因与 issue 的五条方向**无关**：

```
flash_preflight failed: flashtool: flash_tool not found under
resources/flashtool/(该资源不经 git 分发——新装机需按装机手册放置)
```

即装机资源缺口（器具不在该 host 上）；报错自带修复指引 ✓，且同一 host 随后的 405/407/413/415
全部成功 → **已补齐，属瞬态**。

**当前链路版本**（run 404 的 `plan_step` 快照）：`flash_preflight 1.0.4 → flash_firmware
1.3.16 → oobe_skip 1.1.2 → ensure_root 1.0.0 → aee_prepare 1.0.0`——issue 正文写的
`1.0.1 / 1.3.11 / 1.1.0` 之后又迭代了多轮。

### 五条方向逐条落点

| # | issue 方向 | 结论 |
|---|---|---|
| 1 | fingerprint 前就绪等待（`sys.boot_completed` + model 非空重试） | **无近期复现**：09-12 之后 `flash` 步零失败。是否已在某个 1.3.x 版本里加固未逐版核对（本次只读分诊范围止于此） |
| 2 | oobe 等待加固 / 失败重试 | **无近期复现**：17 次 `oobe` 失败全部在 09-11/12；`oobe_skip` 已迭代到 1.1.2 |
| 3 | flash 失败后 USB 死态恢复（11/31 需拔插） | **现场项**：控制面无信号可判 USB 枚举层；失败后主动 `adb reboot`/BROM 复位的效果必须在设备在场时验证——本机做不到，保留为现场跟进 |
| 4 | 判定粒度：flash 成功但后续步失败 → run 统一 FAILED | **仍成立，但属产品裁决**：步骤级 trace 已能分辨失败步（本分诊就是靠它），run 级恒 FAILED。`PlanRun.status` 的枚举里**已有 `PARTIAL_SUCCESS`**（`/results/risk-trend` 等消费方已按它过滤）——是否把「flash 成功、后续步失败」判成它，需要裁决 |
| 5 | 在途 run 的 USB 资源锁释放（首台等锁 190s） | **设计如此，不是泄漏**：`plan_run_abort.py` 明确「RUNNING job 保留 ACTIVE 租约 + 下发 abort 控制命令」，租约由 `device_lease_reconciler._reconcile_aborted_running_jobs` 按宽限回收（ADR-0043 的宽限语义）。190s 是宽限窗表现；若希望它可观测/可调，应另开一条「宽限期可观测」而不是当作 bug |

## Alternatives

- **直接把 issue 关掉**：否决（至少现在不）。第 3、4 条仍是真实待决项——3 需要现场，4 需要
  产品裁决；关掉会让它们消失在没有归宿的地方。
- **按 issue 的五条方向逐条改脚本**：否决。第 1/2 条没有复现，改脚本需要**发布新版本**
  （AGENTS.md 硬不变量：已发布版本不可原地改），在无复现证据的前提下增加版本面成本；
  第 5 条则是把设计语义当缺陷改。
- **顺手把 run 404 那类装机缺口做成门禁**：不做。`flash_preflight` 已经把它变成**带修复指引的
  显式失败**（本次输出即证据），再加一层门禁没有增量。

## Verification

- **只读查询**（生产库，全程 `SELECT`、`LIMIT`）：近 14 天 flash 类 run 的 run/step 状态矩阵、
  失败步骤分布、run 404 的 `step_trace.error_message` 与 `plan_step` 版本快照、各 run 的
  `job_instance.host_id`。
- **代码核对**：第 5 条对 `backend/services/plan_run_abort.py`（租约语义注释）与
  `backend/scheduler/device_lease_reconciler.py`（`_reconcile_aborted_running_jobs` /
  `release_lease` 调用面）逐处核对。
- **未做**：未在设备在场的情况下验证第 3 条（USB 死态恢复）；未逐版本核对 1.3.x 的
  fingerprint/oobe 加固点（issue 第 1/2 条只判「无复现」，不判「已修」）。

## Revisit

- **建议把 issue 收窄为两条**：①（现场）失败后 USB 死态是否需要平台侧主动恢复动作；
  ②（裁决）flash 成功 + 后续步失败是否判 `PARTIAL_SUCCESS`。其余三条在 issue 上留证后关闭。
- **若 1/2 条后再次复现**：先看 `flash`/`oobe` 步的 `error_message` 与当时版本（本次分诊的
  查询即可复用），再决定是否发布脚本新版本加固——不要在无复现的情况下先改脚本。
- **装机资源（flashtool）缺口**：本次是一条瞬态且自带指引的失败；若在多台新机重复出现，
  应把「资源就位」纳入装机验收清单（而不是等 run 失败）。
