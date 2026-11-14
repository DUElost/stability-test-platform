# 初始化回归探针误判与 ReturnDebtConsumer 韧性修复（#3989 Part A）

Status: implemented
Class: bug-fix

载体 issue：#3989（Part A 局部/Agent 侧修复；跨协议收口跟踪见 #3992）。

## Decision

针对设备归还初始化检查（`init_return_check`）与债清偿（`ReturnDebtConsumer`）中的瞬态误判及可观测性缺口，实施最小闭环修复：

1. **`powercycle_disarm`（版本推进至 `1.0.9`）**
   - [`_lib.py:autotesttool_installed`](../../../backend/agent/scripts/powercycle_disarm/_lib.py)：原逻辑在 `pm path` 返回 `rc == 1` 且无 `package:` 行时一概返回 `False`（未安装），当 `adb shell pm path` 因 `device offline` / `device not found` / `Can't find service: package` 等返回 `rc == 1` 并带错误输出时，会被误判为 `autotesttool_installed=False` 并跳过 `stop_task`。现收紧为仅当 `rc == 1` 且 `stdout`/`stderr` 均为空时返回 `False`，带输出则保持 `None`（未知），使 `read_prefs_classified` 维持 `transient`。
   - [`powercycle_disarm.py:_read_prefs_evidence_with_retry`](../../../backend/agent/scripts/powercycle_disarm/powercycle_disarm.py)：为 prefs 只读证据采样增加有界短重试（最多 3 次、间隔 1.0s），仅在 `readback == "readback_failed"` 且 `evidence["kind"] == "transient"` 时触发，并在重试期间打 `progress_stamp`、超过 1 次时记录 `evidence["transient_retries"]`；非瞬态分类（`ok` / `not_installed` / `absent` / `denied` / `empty`）立即返回。
   - 按 ADR-0051 不可变发布单元约定，在 [`tool_manifest.json`](../../../tool_manifest.json) 登记新版本 `powercycle_disarm@1.0.9`（暂不更新 `ONBOARDED["power-cycle"]` 中锁定的 `1.0.8` 与 `verified_device_classes`，待站点 `--publish` 与真机复测刷新 `report_ref`）。
   - 更新 [`disarm_progress_3752.py`](../../../backend/agent/tests/disarm_progress_3752.py) 与 [`return_frozen_3729.py`](../../../backend/agent/tests/return_frozen_3729.py)，按 manifest 最新已登记版本打包验证，并在需要测试 `1.0.8` 契约夹具时使用 verified cache 标记，避免依赖 `git` 历史（兼容 CI `fetch-depth: 1`）。

2. **Agent `device_return.py` 瞬态短重试与结构化日志**
   - 在 [`backend/agent/device_return.py`](../../../backend/agent/device_return.py) 新增 `_is_transient_readback_failure` 判定（仅命中 `result == "failed"` 且 `readback == "readback_failed"` 与 `kind == "transient"`）。
   - `run_init_checks` 与 `ReturnDebtConsumer._execute` 在 `disarm` 命中瞬态回读失败时各做 1 次局部有界重试并打 `WARNING` 日志，避免直接触发 30 分钟 `_RETRY_SECONDS` 退避。
   - 为 `ReturnDebtConsumer._execute` 补齐 `PermitDenied`、`409`（token rejected）、`422`、`RequestException`、`PackageUnavailable` 与重试结果的结构化日志，不弱化 ADR-0061 欠债语义。

## Alternatives

- 直接修改 `ONBOARDED["power-cycle"]["disarm_min_version"]` 为 `"1.0.9"` 并伪造/沿用 `1.0.8` 的真机 `report_ref`：违背 U5/U8 真机实测可信度契约，不采用。
- 在 `autotesttool_installed` 中用黑名单关键词匹配 ADB/PM 错误文本：容易漏掉新 ROM/ADB 错误文案，按「Clean 空输出才判 `False`，否则 `None`」更稳健。
- 对 `ReturnDebtConsumer._execute` 缩短 30 分钟退避或改变 422/409 状态语义：会改变 ADR-0061 退避/拒绝语义（归属于更宽的确认/协议演进 #3992），Part A 只对明确瞬态读回失败做 1 次局部重试。

## Verification

在隔离 worktree `.wt/fix-3989`（分支 `fix/3989-init-return-part-a`）完成以下验证：

- 包注册与清单一键门禁：
  `tools/dev/check_script_packages.py`（40 族树与 `tool_manifest.json` 最新登记等价）与 `tools/dev/check_tool_manifest.py --base origin/main`（46 族 / 278 版本条目，append-only）均通过。
- 定向单元测试与 U4/U5/U8 回归（200 passed）：
  `backend/agent/tests/test_powercycle_disarm_u5_3730.py`、
  `backend/agent/tests/test_powercycle_disarm_v176_3730.py`、
  `backend/agent/tests/test_disarm_u8_3752.py`、
  `backend/agent/tests/test_return_init_u4_3729.py`、
  `backend/agent/tests/test_return_debts_u4_3729.py`、
  `backend/agent/tests/test_powercycle_disarm_transient_3989.py`
  经 `scripts/run_pytest.py` 全部通过（200 passed）。
- `check:quick` 非前端门禁（13 项：`schema-at-head`、`env-inventory`、`ruff`、`compileall`、`layering`、`orphan-models`、`gov-surface`、`ai-work`、`god-files`、`inner-imports`、`tool-contract-fixture`、`tool-manifest`、`transitions`）全部通过；worktree 无 `frontend/node_modules`，未修改前端目录。

## Revisit

- 合入并部署后需在控制面站点对 `powercycle_disarm@1.0.9` 执行 `--publish` 并完成真机验证，再按 ADR-0061 流程刷新 `ONBOARDED["power-cycle"]` 的 `disarm_min_version` / `verified_device_classes`。
- 与 Agent-控制面显式 ACK / 全链路可观测性相关的跨边界演进（原 #3989 B 部分）继续在 #3992 中统一设计。
