# ADR-0051 D7 第 4 片 a/b：撤 STP_FLASH_TOOL_DIR 渲染 + 四消费族旧版本 retired（2026-09-26）

Status: implemented
Class: feature

## Decision

D7 已于 2026-09-26 激活（canary 设备 867：刷机 run 575 / Monkey run 576 均经 `required_tool_injected` 从 tools_cache
取工具；15 个在用计划重指；48/48 主机预热；18:44 全机队回归 `monkey_resource_push 1.1.1` 476 次成功）。本片收过渡尾巴：

**a. 撤 `STP_FLASH_TOOL_DIR` 的 hot-update 渲染**：`_install_dir_env_overrides` 删该键、`AGENT_PATH_ENV_KEYS` 删推送时路径核验；
台账 `flash-tool-dir-env-injection` 转 done（evidence = 上述激活证据）。**不进 §6 / `RETIRED_ENV_KEYS`**：#3402 的删键通道只收
「已移除（无读取点）」键，而本键仍是**步骤级注入键**（flash_firmware 1.3.18 / preflight 1.0.6 读它，由引擎注入包根）——硬塞进 §6
会与「台账键无读取点」守卫冲突，也是语义错误。存量主机 `.env` 旧行**惰性**：新版本被步骤注入覆盖（fail-closed，不回退主机值），
读主机值的旧版本在 b 中退役。

**b. 四消费族 10 个旧版本 `retired:true`**：flash_firmware 1.3.15/1.3.16/1.3.17、flash_preflight 1.0.1/1.0.2/1.0.4/1.0.5、
monkey_resource_push 1.0.0/1.1.0、monkey_test 1.2.2——换 rev 后 scan 停用，杜绝「新装主机（无渲染键）上跑到读主机路径的旧版本」。

`AIMONKEY_RESOURCE_DIR` 的渲染**保留**：其值与 `aimonkey_paths` 代码默认值相同（安装布局键、非登记过渡项），撤渲染只是外观变化，
还会迫使一轮 `--force`；随第 4 片 c（host-resources 通道）一并评估。

## Alternatives

- **按 #3402 的「§6 加行 + RETIRED_ENV_KEYS」删主机旧行**：与 §6「无读取点」语义冲突（刷机脚本仍读本键）；需要的是第二类登记
  「渲染面退役、仍为步骤级注入键」——属 #3402 机制的扩展，已在 #3356 提出，由机制负责方裁决。
- **为摆脱旧名发 flash 新版本改读 `STP_TOOL_FLASHTOOL_DIR`**：只为命名再走一轮版本+重指+真机，收益不抵成本；弃。
- **retire 时保留 plan 13 引用的 1.3.15/1.0.4**：plan 13 已 `[废弃]` 且相关步骤 `enabled=False`，不会派发；重新启用时 PUT 的
  `_validate_script_refs` 会拒绝已停用版本——失败在编辑期而非运行期，故一并退役。

## Verification

- `test_agent_env_sync.py`：两条断言改为反向守卫（渲染与路径核验不得再含本键）；**在 origin/main 旧渲染面上恰红 2**；
  env sync + 删键台账测试 35 passed
- manifest：10 处翻转 diff 恰 10 行；`check_tool_manifest --base origin/main` append-only 绿；`check_script_packages` 35 族等价
- **生产库只读 scan 推演**（新 manifest × 现库）：将停用恰为这 10 个版本、将新建为空、**停用 ∩ 启用步骤引用 = ∅**
- `check_transitions`：8 条 / 4 在途；`check:quick` 见 PR

## Revisit

- **激活（运维，换 rev 时）**：scan 停用 10 个旧版本前，确认无在途 run 的 job 快照引用它们（seed 迁移 × 在途 run 的既有缺口）。
- 第 4 片 c：host-resources 通道下线（hot-update resources 层、Ansible 资源推送、bundle host-resources 分量、`AIMONKEY_RESOURCE_DIR` 渲染）。
- #3356：「渲染面退役、仍为步骤级注入键」的主机旧行清理是否需要第二类登记——待机制负责方裁决。
- plan 13 `[废弃]` 建议 owner 删除或归档（其禁用步骤引用的版本本片已退役）。
