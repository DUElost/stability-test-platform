# Agent Note: #4182 Phase 1 续 — 将 `log_prepare@1.0.0` 接入全部 10 套稳定性内置模板

Status: implemented
Class: feature
Issue: #4182
Date: 2026-11-27

## Decision

在 PR #4209（`log_prepare@1.0.0`）合入后，将 `script:log_prepare@1.0.0` 接入全部 10 套稳定性内置模板（不接入 2 套刷机模板 `flash_unisoc` / `flash_unisoc_bsl`），并严格遵守 #4182 评审裁决的两条边界：

1. **先清旧日志、再开并验证 logger**：
   - `monkey.json` 与 `monkey_watcher_patrol.json`：`log_prepare@1.0.0` 排在 `monkey_setup`（清旧日志）之后、`monkey_launch` 之前；
   - `gpu.json`：`log_prepare@1.0.0` 排在 `gpu_setup`（清旧日志）之后；
   - `mtbf.json`、`powercycle.json`、`powercycle_adb.json`、`sleep.json`、`aging_ddr.json`、`ddr.json`、`standby.json`：`log_prepare@1.0.0` 排在 `sync_device_time` / `unisoc_sysdump_config`（及 `ensure_root`）之后、专项 setup 之前。
2. **专项容量差异由模板表达**：
   - `gpu.json` 显式配置 `{"mobilelog_size_mb": 10240, "ylog_size_mb": 10240}`；
   - 其余 9 套稳定模板配置 `{"mobilelog_size_mb": 4096, "ylog_size_mb": 4096}`；
   - `aee_mode` 缺省不填（保持设备当前值），项目级差异留待 Phase 3（ADR-0029 D4 最小复议）或步骤覆写。

同步在 `tests/test_pipeline_template_script_pins_2865.py` 增加 `script:log_prepare` 版本 pin 守卫，在 `backend/tests/api/test_pipeline_templates_stages.py` 新增顺序与容量契约测试，并更新 `.claude/skills/stability-plan-launch/SKILL.md`。

## Alternatives

1. **把 `log_prepare` 统一固定在所有模板的 `init[3]`**：拒绝。`monkey_setup` 与 `gpu_setup` 内部会清空 `/data/debuglogger/*`，把 `log_prepare` 放它们前面会导致刚开启的日志目录被直接删掉。
2. **在生产尚未完成 `--publish` 与 `scripts/scan` 激活前就合入本 PR**：拒绝。先以 Draft PR 提交并跑齐 CI，待生产完成 `d4d9e403` 切换、`--publish` 发包与 `scripts/scan` 激活 `log_prepare@1.0.0` 后再转为 Ready 合入。

## Verification

- `.venv/bin/python scripts/run_pytest.py backend/tests/api/test_pipeline_templates_stages.py tests/test_pipeline_template_script_pins_2865.py -q` → 全部通过
- `.venv/bin/python scripts/run_gates.py check:quick` → 16 gates 全部通过
- `.venv/bin/python tools/dev/check-internal-ip-leak.py` → 通过

## Revisit

- 待本 PR 合入且各专项稳定运行后，规划旧 `aee_prepare`、`gpu_*` 内联 logger 广播与 `tools/ansible/enable_mobilelog.sh` 的过渡退役。
