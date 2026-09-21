# #2958 输入面：per-host 脚本目标可达集（只读脚本）

Status: implemented
Class: bug-fix

## Decision

新增只读诊断脚本 `backend/scripts/compute_host_script_targets.py`，计算**每台 host 预期会用到
哪些 `(族, 版本)`**（可达集）与相对全集的差集（`n_a`），作为 #2958 第五道闸的**输入面**。

口径（全部经生产库只读实测校准）：

- **全集** = `plan_step.enabled` 引用的版本 ∩ `script.is_active` —— 与运行快照同源
  （`backend/services/plan_dispatcher_core.py` 组快照时过滤 `enabled is False`）。
  实测 **51 版本 / 29 族**：52 → 51 的差集正是 [废弃] plan#13 唯一的
  `flash_firmware@1.3.15`（该步骤同时 `enabled=false`），**无需另设「排除废弃计划」判据**。
- **可达集 = 调度可达 ∪ 历史可达**：
  1. 调度可达：`task_schedules.device_ids` 的设备**当前**绑定的 host（实测生产库仅 2 条 schedule，
     覆盖链式计划）；
  2. 历史可达：近 `--days`（默认 30）`plan_run_host` 出现过的 host（手工/一次性计划的主要来源；
     实测近 30 天 46 台 host / 47 个 plan）。
- **`n_a` = 全集 − 可达集**：全集有、该 host 不会跑到 → 不判红（防假缺口；实测每族实跑 host 数
  1–46 不均，`unisoc_*` 仅 3 台）。
- 只读 SELECT，**不写库**。退出码：0 对账完成 / 2 使用事实不可得（表缺失、方言不支持，**不降级为空集**）
  / 3 工具自身异常。

本 PR **不关闭 #2958**：host 侧核验（复用 `verify_scripts` RPC）、落库、指标/告警仍在该单射程内。

## Alternatives

1. **保守全量矩阵（51/52 × 48 host）**：实现最简，但实测会给不跑 `unisoc_*`（3/46 台）、
   `mtbf_*`（2/46 台）的 host 报假缺口——首轮就会淹没在假红里，故弃。
2. **只看历史可达**：新 host / 维护窗归队 host 无历史 → 可达集为空、n_a 全集（`172-21-15-91`
   实测即 0 可达）；只有调度面才能覆盖「即将首次派发」。反之只看调度面则覆盖不到手工计划
   （生产库仅 2 条 schedule）。→ 取并集。
3. **用 `plan_run.plan_snapshot` 的历史步骤**：会把已被重指掉的旧版本算进可达集（快照是派发时冻结），
   与「预期会用到」的语义不符；改用当前 `plan_step`。
4. **改 `agent_code_sync_status`**：ADR-0040 v1.1「判据唯一性」有前端测试钉死
   （`frontend/src/components/network/ExpandableHostTable.test.tsx`：revision 不等不得渲染 drift），
   脚本级语义不得塞进去 → 独立脚本/独立面。

## Verification

- 单测：`backend/tests/test_host_script_targets_2958.py`（6 例，纯函数层：全集交口径 / `device_ids`
  三形态解析 / 调度与历史两层可达 / 可达集与全集求交 / 无来源 host 如实暴露 / 汇总计数）。
  `./scripts/run_pytest.sh backend/tests/test_host_script_targets_2958.py -q` → **6 passed**。
- 生产库只读实跑（`origin/main 8ec2bb46` 工作树，控制面 `.env.backend`）：
  `python -m backend.scripts.compute_host_script_targets --full`
  → `hosts=48 可达格=1376（min=0 max=38）并集覆盖=51/51 未覆盖=0`。
- 取串守卫登记：`tests/test_sync_database_url_scripts.py` 的 `HELPER_SITES` 增本脚本
  （该守卫强制「同文件既取 DATABASE_URL 又建同步 engine」的站点必须登记，防 `MissingGreenlet`）。

## Revisit

- **落库/告警面未做**：本脚本只产出可达集，不落库、不计指标。第五道闸主体（新表 + `/metrics`
  抓取期 gauge + 告警）仍待 #2958 认领；频率、告警形态、UI 载体三项待裁决。
- **无归属 host 的语义**：`min=0`（`172-21-15-91`）当前显示为「可达 0 / n_a 51」。UI/告警侧必须
  区分「无归属（没设备没跑过）」与「缺文件」——前者不是故障。
- **`--days` 窗口盲区**：窗口小于计划周期时会把「长周期计划」误判为不可达；默认 30 天对当前
  链周期（1h）安全，若未来出现 >30 天的低频计划需复核。
