# plan_step 重指漂移视图（#3030）：第 4 道账 + 两轴分类

Status: implemented
Class: process

## Decision

「合入 → 到部署树 → scan 注册 → `plan_step` 重指」四道里，第 3 道已有 `--pending-activation`
（#2931/PR #2939），**第 4 道无账**：存量 Plan 不随模板更新迁移，实测 **24 族 / 155 步 /
44 Plan** 落后磁盘 head，且 `plan 54` 于 09-20 重指到 `powercycle_setup 1.2.1` 后，09-21
head 推进到 `1.2.2`（PR #3006）即**再次落后**——一次性重指会衰减，必须有常设账。

在 `backend/scripts/check_unreferenced_script_versions.py` 加 **`--plan-step-drift`**（只读），
判据按 owner 两轴裁决（2026-09-21，#3030 的「两轴裁决」评论）：

- **轴 2 活跃度**（以 PlanRun 为主、schedule 仅补充）：`≤14d` 活跃 / `≤30d` 半活跃 /
  更早或从未跑 = 历史（不追）。三态各自计数，历史桶不计红。
- **轴 1 Δ 类型**（优先级 `review_required` > `metadata_diff` > `metadata_compatible`）：
  登记表命中 → `review_required`（带 reason + 复查期）；DB 参数元数据有差异或库缺行 →
  `metadata_diff`；其余 → `metadata_compatible`。
- **冻结登记表 `_PLAN_PIN_REVIEW`**（key = `family:<name>` / `plan:<id>`）：承载 (a) 参数/预算
  不兼容与 (c) 有意钉旧两类冻结；**复查期过期（含值不可解析）在报告里单列「须重新裁决」**。
  种子项一条：`family:check_device`（追至 ≥1.0.2 需同族步骤 timeout ≥180s，#2981）。
- 只读、账本非门禁（exit 0；`STP_SCRIPT_ROOT` 未设 = 无从判定 exit 2，与 `--pending-activation`
  同姿势）；退役判红语义仍归 #735 的 `--guard`。

## Alternatives

1. **另建新脚本**（与 #2958 的 `compute_host_script_targets.py` 平行）：否决——本视图与
   `--pending-activation` 同源（同一磁盘 head 枚举、同一 DB URL 解析与只读纪律、同一
   `version_key` 数值序），并入现有工具可复用这些既有契约；#2958 那条是新数据源（agent RPC）
   才独立成脚本。
2. **自动判定参数不兼容**（只对拍 DB `default_params`/`param_schema`）：否决——实测 35 组里
   34 组"元数据一致"，而 `check_device` 恰在"一致"桶里却**不安全**（150s 内建预算写在代码
   默认值与 docstring，DB 查不出）。故引入 `Δ 类型` + 登记表，而不是假装能全自动。
3. **活跃度判据用 enabled schedule**：否决——实测 44 个落后 Plan 里只有 1 个有启用 schedule，
   只按 schedule 判会漏 43/44（97.7%）；chains 触发的 run 才是主要信号。
4. **把视图做成门禁**（落后即 exit 1）：否决——与 #2931 同纪律（账本非门禁）；「追 head vs
   冻结」是业务裁决，门禁化会把合法冻结项天天判红，噪声淹没真缺口。

## Verification

- `pytest backend/tests/test_plan_step_drift_view_3030.py -q` → **15 passed**（纯函数 + 合成事实）：
  判据自证（pin==head → 消失；族无磁盘 head → 跳过并计数）、数值序 head、两轴边界
  （14d/15d/30d/31d、schedule 补充）、Δ 三态与优先级（plan 级覆盖 family 级）、
  复查期过期与不可解析两种形态。
- **反向验证（mutation，均"改坏即红"后还原）**：
  ① 短路 `pinned == head` → `test_pin_at_head_not_listed` FAILED；
  ② `PLAN_STEP_DRIFT_ACTIVE_DAYS` 14→4 → `test_activity_boundaries` FAILED；
  ③ 摘掉登记表优先分支 → `test_builtin_registry_marks_check_device_review_required` +
  `test_review_registry_takes_priority_over_metadata_diff` FAILED。
- **真实库只读实跑**（生产控制面，`DATABASE_URL` 取自 `.env.backend`，仅 SELECT）：
  `落后 155 步 / 44 Plan（活跃 63、半活跃 75、历史 17；需人工核 35、元数据差异 1）`——
  与独立实测（155 步 / 44 Plan、check_device 35 步、install_apk 1 步元数据差异）一致；
  `plan 21`（`.89` 在跑的巡逻链）逐行带 `check_device … 需人工核…（复查 2026-10-21）`。
- `ruff check`（工具 + 测试）→ All checks passed；
- `python scripts/run_gates.py check:quick` → **[OK] 12 gates**；
  `check:pr` → **[OK] 21 gates**（含 agent-tests / pr-migrate 空库迁移 / seed 身份对拍）。

文档与 skill 同步：`docs/development/script-versioning.md`（第 4 道收尾判据 + 两轴口径）、
`.claude/skills/script-version-lifecycle/SKILL.md`（后置验证补第 4 道与复查期两条）。

## Revisit

- 可选观测面未做：把本视图接 `stp-script-guard.timer` textfile 家族出 gauge（>0 且持续 N 天报）；
- 登记表种子项 `family:check_device` 复查期 **2026-10-21**——到期须重新裁决（配套修订是那批
  30s timeout 的步骤，属独立工作）；
- 若 #2958（PR #3016）落地，「部署树 vs 主机 vs 引用」三面宜在同一处报告，避免三处各看一半；
- 视图落地后首批追平工作（20 活跃 + 21 半活跃 Plan）本身是生产写操作，另行排期执行。
