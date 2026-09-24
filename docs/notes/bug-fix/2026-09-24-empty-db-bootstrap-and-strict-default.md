# ADR-0051 空库自举断链与 strict 默认：落地审查两条高风险的修复（2026-09-24）

Status: implemented
Class: bug-fix

## Decision

并行会话的落地审查提出两条高风险，**隔离空库实测复现后确认成立**，本单修复：

1. **sync 重写丢了首扫回填通道（我于 Phase 3 #3208 引入的回归）**。seed 迁移写的 47 行
   只有 entry sha（support/caps/package_sha256 为空）；Phase 3 的 `sync_scripts_from_manifest`
   把「行与包任一字段不等」一律判 conflict——53 处 support/caps 差异（实测 46 计入 conflicts）
   短路了包身份回填 → 新站点首扫 conflict 满屏、strict 下全部版本无包身份被拒执行，
   **空库 → scan 自举链整段断裂**。修复 = 恢复原 `scan_script_root` 的首扫回填先例并强化：
   入口 sha 一致时，**凡 changed 维度必为「行为空、包有值」⇒ 单轮全量回填（support、caps、
   package_sha256 同轮）**；行与包双方有值且不等 = 真漂移，仍 conflict；conflict 不再短路回填。
   （原先例逐维分轮需 3 轮 scan 收敛 39 行，实测二扫仍剩 5 行无 sha——合并为单轮。）
   守卫测试 `test_seed_rows_backfill_support_caps_and_package_sha_in_one_pass` 钉三事：
   首扫单轮收敛、二扫幂等、真漂移不被回填吞掉。
2. **`STP_SCRIPT_PACKAGES` 缺省 off + tree 回退指向已删目录**。Phase 3 删了
   `agent/scripts/`，漏配/新装/重装主机默认走死路径。修复 = 默认即 strict、`_fallback`
   与 tree 分支整体删除（包不可用一律 `PackageUnavailable` → exit 2 显式失败）；
   `off`/`on` 仅剩告警别名，解析删除挂 fleet env 模板清理续单。台账
   `script-packages-off-on-modes` 本单转 done。
   测试面：engine/barrier/process-group 流程用例的 Fake registry 无包身份 → autouse stub
   直映射（分层：包解析由 `test_script_packages` 专测）；包面用例改「缺省即包模式/不可用=exit 2」。

## Alternatives

- **给 47 行 seed 全部补 support/caps 字面量（改 seed 迁移）**：不可行——迁移链不可改写（#2258），
  且「包是 metadata 唯一权威」正是本架构方向；行空包有 ⇒ 回填，语义自洽。
- **strict 但保留 tree 回退当兜底**：弃——回退目标在 Phase 3 后物理不存在，留着只是把
  「显式失败」换成「误导性静默」；审计正是抓到这类静默。
- **把 off/on 解析一并删**：留半格——fleet 存量 `.env` 里有显式 off/on 字面，硬删让
  `package_mode` 对旧值静默宽容反而丢告警；本单先「别名+告警」，fleet env 模板清理单删解析。

## Verification

- **隔离空库全链重放**（临时库 `stp_audit_0924`，alembic head → 首扫 → 二扫）：
  修复前复现审查数字（created=164 / conflicts=46 / 活跃无包 sha 20→残留 5 / 需多轮收敛）；
  修复后首扫 `created=164, skipped=47, conflicts=0, package_backfilled=47`，
  二扫全幂等，`活跃∧无包sha = 0`（211 行 / 184 活跃）。临时库已删。
- Agent 全套 → **2137 passed**；sync/api/presence/transitions 相关 81 passed；
  `check:quick` **16 gates OK**；治理守卫 S1–S15、`check_transitions`（6 条 / 4 在途）绿。
- 审查矩阵其余项的处置：fleet strict 持续可观测（#3222）与 flashtool/aimonkey/legacy env
  收敛不在本单（后者台账已挂 due）；`default_params` 覆盖差与 manifest retired 策略作为
  Phase 4b/5 数据项记入 ADR v1.4 遗留段。

## Revisit

- 生产 fleet 本单合入后需一轮 `--force` 热更（script_packages.py 变更在 agent 载荷面），
  并在部署后抽验一台 `.env` 无开关主机行为=strict（新装形态代理验证）。
- fleet env 模板清理单：删 `off|on` 别名解析、`STP_AGENT_SCRIPT_PACKAGES` 源键与示例行。
