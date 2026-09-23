# flash_firmware 在包模式下 flashtool 回退解析断链的过渡止血（2026-09-23）

Status: implemented
Class: bug-fix

## Decision

ADR-0051 Phase 3 落地后（fleet 48/48 `STP_SCRIPT_PACKAGES=strict`、`agent/scripts/` 全删），
`flash_firmware.py::_locate_flash_tool_dir` 的 fallback 从
`script_dir/../../../resources/flashtool/…`（tree 模式下正确）变成指向
`<install>/resources/…`——**少一级、不存在**；而 5 组在用刷机 plan 全部不带
`flash_tool_dir` 参数、控制面也从未注入过 `STP_FLASH_TOOL_DIR`（对照：aimonkey 的
`AIMONKEY_RESOURCE_DIR` 一直在 `_install_dir_env_overrides` 注入所以没断）。
下一次刷机执行必以 `flash_tool executable not found` 失败。

止血：`_install_dir_env_overrides` 按安装布局注入与脚本 fallback **等值**的
`STP_FLASH_TOOL_DIR`（`{root}/agent/resources/flashtool/SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100`），
并把该键加入 `AGENT_PATH_ENV_KEYS`——主机缺 resources/flashtool 在**推送时**暴露
（`env_paths_missing`），不再拖到刷机时。**标注为过渡**：终态 = flashtool 入包
（`tool_manifest.json` + 站点 packages/）+ flash 族新版本按 `STP_AGENT_INSTALL_DIR`
（pipeline_engine 已注入子进程 env）显式解析，落地后删除本注入。

## Alternatives

- **给每个 plan 加 `flash_tool_dir` 参数**：弃——写死绝对路径进 5 组 plan，站点化（ADR-0041）
  时每台部署根不同，制造更难看的硬编码。
- **直接出 flash 族新版本（终态方案）**：本可一步到位，但脚本改动 + `--register` 新版本 +
  plan 重指是独立一坨；刷机链断链是当下事实，先用最小面恢复，再从容做终态。
- **把 STP_FLASH_TOOL_DIR 加进 `.env.backend` 让人配**：弃——路径是安装布局推导的，
  与人无关；`_install_dir_env_overrides` 正是这类派生路径的既有落点（aimonkey 先例）。

## Verification

- `backend/tests/services/test_agent_env_sync.py` + `test_host_updater.py` +
  `test_script_reference_check.py` → 62 passed（layout 断言、`STP_FLASH_TOOL_DIR ∈
  agent_path_keys_to_verify` 正反两向）；`check:quick` 15 gates OK；
  `env_inventory --check` OK（手写表登记，未污染生成块——首版误插生成区已移出）；
- canary 真机核对过断链事实：`resources/flashtool/SP_Flash_Tool_*/flash_tool` 存在、
  主机 `.env` 无该键、5 组 plan 无参数（只读查库）。
- 合入部署后（新 rev 切换 + force 推送）：抽查主机 `.env` 应含 `STP_FLASH_TOOL_DIR=`
  指向存在目录；刷机 dry 验证走真机留待下次刷机窗口（`env_paths_missing` 为空即证明路径在位）。

## Revisit

- Phase 4 flashtool 入包 PR 落地时：删除本注入、`AGENT_PATH_ENV_KEYS` 同步、文档行改「已移除」
  进退役台账（test_removed_env_keys 的「台账键须有出处」）；
- 若站点部署根不装 SP_Flash_Tool 版本升级件，注入的版本目录名与 resources 实装版本会漂移——
  `env_paths_missing` 在推送时报红即是信号（这正是加 path-verify 的原因）。
