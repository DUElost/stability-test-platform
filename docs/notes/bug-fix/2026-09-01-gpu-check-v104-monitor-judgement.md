# Agent Note: gpu_check v1.0.4 monitor 模式判词（#728，**事后补记**）

Status: implemented
Class: bug-fix
Issue: #728
补记说明: 本 note 为**事后补记**，来源为 #788 第 9 项（原 PR #728 属非平凡变更——
行为修复 + seed 迁移 + 新版本目录——却未随 PR 附 note）。内容**依据 PR #728 正文与
合入提交复述，未新增当时未做出的判断**；Decision / Alternatives 两节按原正文的
「改动」段回溯整理，凡原正文未记录之处均如实标注。

## Decision

gpu_check v1.0.4 的判词顺序改为「先判是否仍在跑，再判结果形态」：

1. 缺 `GPU_RUN_END` → `running`；
2. `OK (N tests)` 文本：N > 0 → `ok`；N == 0 → `no-tests`（保留 8/31 加入的空跑防护）；
3. `Process crashed` → `crashed`；
4. **protobuf `test_result=true` → `ok`**（monitor 模式的正常完成形态）。

根因：循环脚本 `am instrument -w -m`（monitor 模式）只输出 protobuf，
**正常完成时没有 `OK (N tests)` 文本**；v1.0.3 因此把「正常完成」误判为
`OK (0 tests)` 空跑 → 2026-09-01 全量 276 台链式执行（run 330）全量失败。

（run 323「成功」之谜的解释：当时 Agent 侧判定为 v1.0.2——只查 `GPU_RUN_END`、
不检测空跑；v1.0.3 补上「空跑检测」后，monitor 模式的正常完成被误伤。）

## 影响与配套

- 新版本目录 `backend/agent/scripts/gpu_check/v1.0.4/`（`gpu_check.py` / `_lib.py` /
  `capabilities.json`）——按 ADR-0020 走新版本，未原地改 v1.0.3；
- seed 迁移 `backend/alembic/versions/k1l2m3n4o5p6_seed_gpu_check_v104_monitor_mode.py`：
  落库 v1.0.4 并 deactivate v1.0.3（空库 `upgrade head` 验证通过）；
- `backend/agent/tests/test_gpu_power_sleep_resources.py` 增 4 用例
  （monitor ok / crashed / OK 文本回归 / running）。

## Alternatives

原 PR 正文**未记录被否方案，本节不臆造**。仅能从判词顺序读出一项取舍：
把 `OK (N tests)` 的 `N == 0 → no-tests` 空跑防护**保留在 protobuf 分支之前**——
即「先按文本判，只在文本缺席时才信 protobuf」，以避免 monitor 兼容性把 8/31
刚加上的空跑检测整个回退掉。

## Verification

- 真机实证（2026-09-01，device 1）：手动 `am instrument` 输出 `test_result=true` +
  `testcase_name`（无 `OK` 文本、无 crash）→ v1.0.4 判 `ok`；
- `python -m pytest agent/tests/ -q` = **1428 passed**。

## Revisit

- 该脚本后续已演进到 v1.0.5 / v1.0.6 / v1.0.7。本 note **只记录 v1.0.4 当时的判据**，
  不追述更高版本的判词现状——避免再次产生「文档描述已变代码」的同型漂移（见 #788）。
