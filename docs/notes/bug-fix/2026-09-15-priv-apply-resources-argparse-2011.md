# stp-agent-priv `apply-resources` argparse 未接线修复（#2011）

Status: implemented
Class: bug-fix

## Decision

**1）修接线**（`backend/agent/stp_agent_priv.py`）：`apply-resources` 的 `add_parser`
返回值此前被丢弃、`--digest` 被误挂到 `write-digest`，导致该子命令无 `--staged`、
真实调用 `exit 2`；`cmd_apply_resources` 又读 `args.staged`。现改为
`p = sub.add_parser("apply-resources", ...)` + `p.add_argument("--staged", required=True)`；
`--kind/--digest` 留在 `write-digest`（`cmd_write_digest` 正是读 `args.digest`）。

**2）闭合探针盲区**：新增 `_SUBCOMMAND_CONTRACT`（每个子命令的**代表性真实 argv**）
与 `_validate_parser_contract()`（逐个 `parse_args` 断言：命令可解析、选项确实接线、
契约表与注册表互为完备），并把结果并入 `selftest`。控制面远端脚本开头即跑
`sudo -n "$PRIV" selftest`（失败 → 回落 legacy）——本类「探针过、真调用挂」的缺陷
**从此 fail-safe**，而不是带病上阵后中止整段热更新。

**3）测试落位 PR 路径**：新增 `tests/test_agent_priv_parser_contract.py`（根 `tests/`
的离线子集在 `pr-agent-tests` 中执行）：
- 接线断言：`apply-resources --staged` 可解析；缺 `--staged` 报 `required`（`--help`
  探针做不到的断言）；`write-digest --kind/--digest` 接线；
- `selftest` 失败面：契约损坏时返回 1 并打印 `SELFTEST_FAIL`；
- **两侧口径交叉核验**：从 `_build_remote_script()` 生成的远端脚本里抽取全部
  `"$PRIV" <cmd> --opts` 调用，逐个断言选项存在于对应子解析器（#2011 的根因是这条缺失）。

## Alternatives

- **只修 wiring、不加契约校验**：同类缺陷（丢 `add_parser` 返回值/参数挂错）仍可静默
  通过 `--help` 探针 → 否决。
- **把 `apply-resources` 的存在性探针从 `--help` 改成真实参数调用**：会真跑 rsync
  或落在参数校验上，副作用与误判都不可控；且它与「本版 wrapper 是否支持该子命令」
  的语义混淆 → 否决，改为「selftest 校验接线 + 探针只做存在性」。
- **给 `selftest` 加 `--capabilities` 输出供探针消费**：能力面更大，且要求老 wrapper
  与新控制面同批升级 → 暂不做（若后续子命令继续增多再评估）。

## Verification

- `pytest tests/test_agent_priv_parser_contract.py`：**7 passed**（交叉核验项需 env，
  CI `pr-agent-tests` 已提供；本机 worktree 无 `.env.backend` 时该项显式 skip 并给原因）
- **反向验证**：把交叉核验断言跑在**修复前**的 parser 接线上 → 精确抓出
  `('apply-resources', '--staged')`（能抓住 #2011 ✓）；修复后零告警
- `selftest` 语义：契约问题经 `STP_AGENT_PRIV_SELFTEST_FAIL` 暴露、退出码 1 → 控制面
  回落 legacy（`host_updater` :213 既有分支）
- `python scripts/run_gates.py check:quick`：见 PR
- **pending（运维步骤，不在本单）**：wrapper 位于安装目录之外、只由
  `tools/ansible/playbooks/update_agent.yml` 铺开——修复版 wrapper 铺开后再做一次
  **资源层灰度**（1→5→全量）验证 `apply-resources` 端到端；铺开前主机仍走 legacy
  rsync 回落（现状即可用，但 wrapper 通道未被验证）

## Revisit

- 若再现「探针过、真调用挂」的其他形态：契约完备性检查会在新增子命令忘登记时红
  （`subcommand X not covered by contract table`），届时按提示补表即可；
- 资源层灰度完成后，把结果回填 #2011/#1963 并在 ADR-0040 的 P2 落地状态上留痕；
- 若子命令继续增多导致 `selftest` 变慢（当前为纯内存 parse），再评估抽成
  `--capabilities` 机器可读输出。
