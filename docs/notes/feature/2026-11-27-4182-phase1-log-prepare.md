# Agent Note: #4182 Phase 1 — 统一设备持续日志准备脚本 `log_prepare@1.0.0`

Status: implemented
Class: feature
Issue: #4182
Date: 2026-11-27

## Decision

落地单入口、跨芯片参数化的设备持续日志准备脚本族 `log_prepare@1.0.0`（`backend/agent/scripts/log_prepare/log_prepare.py` + `capabilities.json`），同步登记 `tool_manifest.json` 并在 `backend/services/script_tags.py` 归入 `common_init`：

1. **职责边界清晰**：
   - 只负责设备持续日志（MTK `mobilelog` + 可选 `aee_mode`；展锐 `ylog`）；展锐 Full Sysdump 继续由已上线的 `unisoc_sysdump_config@1.0.0`（#4181）管理，不重复实现。
   - 不改动既有模板——按 #4182 裁决与 ADR-0051 流程，先合入脚本并完成 `--publish` / `scripts/scan` / 预热，再另立 PR 按「先清旧日志、再开并验证 logger」顺序接入专项模板。
2. **MTK 分支（基于荣耀 `MLD_NX3`/`MLD_NX2`/`ELA_LX2`/`ELA_LX3` 与传音 `INFINIX_X6851` 实机证据）**：
   - `aee_mode` 默认为 `null`（保持设备当前值，不强制 `adb root`）；显式配置（如 `"3"`）时才提权 `adb root` 并强回读 `persist.vendor.mtk.aee.mode`。
   - 通过 `com.debug.loggerui.ADB_CMD` 广播设置 `set_total_log_size_<MB>`（默认 `4096`，GPU 等专项可通过模板传 `10240`）、`set_auto_start_1|0`、可选 `mtk_sublog_cmd` 与 `start`。
   - 用权威只读属性 `vendor.MB.running == "1"`、`vendor.MB.realpath`、`vendor.MB.sublog`（及 root 时 `com.debug.loggerui` `shared_prefs`）做回读验收；`development_settings_enabled` 在 `finally` 中恢复原值。
3. **UNISOC 分支（基于 `P525AA MLY_LX3`/`MLY_LX2` 与 `A57 Z2581` 实机写入与重启保持验证）**：
   - 通过实机验证的 `/product/bin/ylogctl`（YLog Server `5.0.0`）配置 `rotatelog`、`aplogmaxsize <MB>`（64..10240）与 `enable`。
   - 强制解析 `ylogctl query` 与 `ylogctl space`：不仅要求 `status=enable`、`file != NULL`、`APLogMaxSize == ylog_size_mb`，还强制要求 `ylog_required_streams`（默认 `["android", "kernel"]`）全部为 `Open`——拦截 `A57 Z2581` 上 `ylogctl enable` 留下 `android`/`kernel` 关闭的静默坑。
   - 若子流关闭：`userdebug`/`eng` 自动执行 `ylogctl resetsettings` 并重设容量/轮转/启用；若仍关闭（如 `user` 包），提权更新 `/data/ylog/ylog.conf` 并重启 `ylog`，最终未收敛即 fail-fast 报错。

## Alternatives

1. **按项目或专项复制多份日志准备脚本（如 `log_prepare_honor` / `log_prepare_unisoc_a57` / `log_prepare_gpu`）**：拒绝。会造成脚本族爆炸与 ADR-0051 维护负担；芯片差异由单族内部路由吸收，容量/模式差异通过步骤参数（及未来 Phase 3 项目 `log_profile`）表达。
2. **仅检查 `ylogctl enable` 返回码或 `size > 0`**：拒绝。`A57 Z2581` 实机证实 `sgm`/`sysinfo`/`trustlog` 仍会让 `.ylog` 体积增长，但 `android`/`kernel` 实际处于 `Close`，必须逐流解析 `ylogctl query`。
3. **本 PR 同时把 `log_prepare@1.0.0` 写入 9 套内置模板**：拒绝。`log_prepare@1.0.0` 尚未完成生产 `--publish` 与 `scripts/scan` 注册，过早写入模板会触发未注册版本校验失败；合入并完成发包激活后再单开 PR 接入模板（并按「清理步骤后置 logger」顺序排布）。

## Verification

- 单元测试与守卫：
  - `.venv/bin/python scripts/run_pytest.py backend/agent/tests/test_log_prepare.py tests/test_agent_clock_stub_guard_3202.py backend/tests/api/test_pipeline_templates_stages.py backend/tests/services/test_param_docs_content.py -q` → `151 passed, 1 skipped`
  - `.venv/bin/python tools/dev/check_script_packages.py` → 47 个族树与 `tool_manifest.json` 一致（`log_prepare@1.0.0 sha=ffd1ab8afd58`）
  - `.venv/bin/python tools/dev/check_tool_manifest.py --base origin/main` → 通过（append-only）
  - `.venv/bin/python scripts/run_gates.py check:quick` → 16 gates 全部通过
  - `.venv/bin/python tools/dev/check-internal-ip-leak.py` → 通过
- 实机验证（无凭据/真实序列号脱敏）：
  - `P525AA MLY_LX2`（`ums9230`, `userdebug`, Android 16）：`log_prepare.py` 实跑 `success=true`，`ylog_version=5.0.0`，`aplog_max_size_mb=4096`，`android=Open`，`kernel=Open`，且 `adb reboot`（68.1s）后配置与日志产出持续保持。
  - `A57 Z2581`（`ums9230`, `userdebug`, Android 16）：前置实测确认 `ylogctl resetsettings` + `aplogmaxsize 4096` 修复子流关闭坑，`log_prepare.py` 实跑 `success=true`，`aplog_max_size_mb=4096`，`android=Open`，`kernel=Open`。

## Revisit

- 本 PR 合入后按 `script-version-lifecycle` SOP 执行 `--publish --packages-root /mnt/stp-aee/packages` 与 `POST /api/v1/scripts/scan` 完成站点激活，再另立 PR 将 `log_prepare@1.0.0` 接入各稳定专项模板（位于清理旧日志步骤之后），并规划 `aee_prepare`、`gpu_*` 内联 logger 与 `tools/ansible/enable_mobilelog.sh` 的过渡退役。
- 后续进入 #4182 Phase 3（ADR-0029 D4 最小复议）时，将 `Project` 的 `log_profile` 预设直接映射到本脚本的参数键（`mobilelog_size_mb`、`aee_mode`、`mtk_sublog_cmd`、`ylog_size_mb` 等）。
