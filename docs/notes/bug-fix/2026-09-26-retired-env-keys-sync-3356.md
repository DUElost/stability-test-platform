# 渲染面退役键删键下发 + 部署 --force 判据（#3356 候选 2+3）

Status: implemented
Class: bug-fix

关联：[#3356](https://github.com/DUElost/stability-test-platform/issues/3356)（owner
2026-09-26 裁决：本期做候选 2+3、候选 1 不做带复议触发器）、
[#3288](https://github.com/DUElost/stability-test-platform/issues/3288)（D7 第 4 片删键
退出依赖本机制）、
[ADR-0051](https://github.com/DUElost/stability-test-platform/blob/main/docs/adr/ADR-0051-release-unit-and-content-addressing.md)
v1.7 D7、ADR-0040（收敛判据**未被本单改动**）。

## Decision

- **同源**：`RETIRED_ENV_KEYS`（`backend/services/agent_env_sync.py`）与
  `environment-variables.md` §6 台账**逐键相等**，由
  `tests/test_removed_env_keys.py::test_retired_env_keys_mirror_the_ledger` 双向钉死
  （运行时多出 = 未登记就删主机行；台账多出 = 已登记却永不清理）。§6 是唯一登记面，
  运行时表只是其主机侧执行面，不另立第三份清单。
- **生产路径**：删键发生在特权 wrapper `stp_agent_priv.py::_sync_env`（issue 正文引用的
  `merge_env_overrides` 不是生产路径——原作者已更正）。控制面经 `host_updater` 每次
  sync-env 以 `--retired-keys-b64` 下发清单；wrapper 在 overrides merge **之后**整行删除
  （两集合不相交由双侧判据保证），实际删到的键经新哨兵 `STP_ENV_RETIRED_REMOVED` 回报，
  进 hot-update 响应 `env_keys_retired` 与 agent_version_info 审计 details。
- **fail-closed 面收口（#2319 同款）**：`sync-env/retired-keys` 作为**参数级**能力标记
  进 `cmd_capabilities` 输出与远端脚本前置判据——旧 wrapper 只报子命令名，子命令级判据
  对「同名 sync-env 缺新 flag」没有判别力；不前置就会在 `set -e` 脚本中段被 argparse
  拒绝（apply-code 已执行、restart 未执行的半态）。
- **镜像同步**：`merge_env_overrides` 扩为三返 `(new_lines, updated_keys, removed_keys)`，
  删除语义与 wrapper 逐字对齐（含相交集 ValueError fail-closed），docstring 标注
  「非生产路径」以免再被当成事实源引用。
- **候选 3（自觉级）**：`check-deploy-readiness.py --diff-base <部署基线>` 对触及
  `backend/services/agent_env_sync.py` 的 diff 打 WARN「本批 hot-update 必须
  `--force`」（渲染面不进 ADR-0040 收敛判据，机制面由候选 2 承担，提示不改退出码）；
  `control-plane-deploy` SOP §3 增同款判据与 wrapper 安装前置说明。

## Alternatives

- **候选 1（env 层 digest 并入收敛判据）**：按裁决不做——改变 ADR-0040 收敛语义须先修
  ADR；复议触发器已记录在 issue。
- **wrapper 侧硬编码退役表**：弃——wrapper 是 root 单文件、随 Ansible 分发，硬编码会让
  键表更新依赖全 fleet 重装 wrapper；按调用传参后，键表演进只动控制面。
- **RETIRED_ENV_KEYS 初始为空、由 #3288 第 4 片再填**：弃——与裁决「与 §6 同源」不符，
  且空表会让「删键通道」长期处于未验证态（现站 5 键对主机是 no-op，正好当机制先行验证）。
- **守卫豁免**：为退役表引用在 `test_removed_env_keys` 开豁免面——弃——同线标记
  （`# 已移除`）即合规，扩豁免面是给未来留口子。

## Verification

- `pytest tests/test_removed_env_keys.py backend/tests/services/test_agent_env_sync.py
  backend/tests/services/test_host_updater.py`（70 passed，含台账镜像双向、渲染面不相交、
  wrapper 删键/幂等/相顶拒绝、脚本载荷与能力标记钉子）；
- `pytest tests/test_agent_priv_*.py tests/test_agent_priv_parser_contract.py
  backend/tests/api/test_hot_update_noop_gate_1907.py backend/tests/services/test_artifact_digest.py
  backend/tests/services/test_agent_version_info.py`（154 passed）；
- `pytest backend/tests/api/test_hosts.py backend/tests/api/test_upgrade_gate_api.py`
  （70 passed，API 响应加键无形状破坏）；
- `python scripts/run_gates.py check:quick`（16 gates 全绿；schema-at-head WARN =
  worktree 未配 DATABASE_URL，eslint 依赖 symlink 主树 node_modules 后实跑通过）；
- 发射面唯一性由 `test_agent_priv_parser_contract.py` 既有钉子扩展
  （`STP_ENV_RETIRED_REMOVED` 只由 wrapper 发射、远端脚本无字面量）。

## Revisit

- **部署顺序硬约束**：本 PR 合入部署后、下一次 hot-update 前，目标机必须先经
  `update_agent.yml` 装新 wrapper（旧 wrapper 缺 `sync-env/retired-keys` → 脚本在
  任何写动作之前 fail-closed 并给指引；fail 安全但会阻断批量）。
- 删键依赖 `--force` 或 digest 漂移才会随热更新到达主机（候选 1 不做的直接后果）——
  再出现一次「env-only 变更未随热更新到达主机」即触发候选 1 复议（issue 已记）。
- #3288 第 4 片退役 `STP_FLASH_TOOL_DIR` / `STP_UNISOC_*` 时：先删渲染面（会立即被
  「退役键 ∩ 渲染面 = ∅」测试拦住颠倒顺序），再在 §6 加行 + 同步 `RETIRED_ENV_KEYS`。
