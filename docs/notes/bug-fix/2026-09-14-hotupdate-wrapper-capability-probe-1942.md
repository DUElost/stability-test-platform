# 热更新 wrapper 能力协商与失败 message 纳入 stderr（#1942）

Status: implemented
Class: bug-fix

## Decision

2026-09-14 14:2x 从 `/hosts` UI 批量热更新 48 台：**34 成功 / 14 失败**，页面与审计只见
`Remote script failed (exit=2)`。实机与日志定位：

- 14 台主机的 `/usr/local/sbin/stp-agent-priv` 是 fe1b1300（#1250 首版，09-11 经 Ansible
  布下），**无 `write-digest` 子命令**（现状版本 511fcb18 才引入，ADR-0040 D2）；
- 远端脚本在**探活通过后的尾部**调用 `sudo "$PRIV" write-digest --digest ...` → argparse
  拒绝 → exit 2；`set -e` 使整脚本以 exit 2 结束。14 台实际**代码已同步、服务已重启**
  （`VERSION`/心跳均 `ae77ca90`、服务 active），失败只在 digest 落盘；
- `_remote_failure_message` 只扫 stdout 的 `ERROR:` 行，而 wrapper 拒绝
  （`STP_AGENT_PRIV_ERROR:`）与 argparse 用法错误全在 stderr → 退化为无因文案
  （#1253 Note 的 Revisit 项由此触发）。

修复（两处，均在 `backend/services/host_updater.py`）：

1. **能力协商前置**：远端脚本在 wrapper 分支且 `ARTIFACT_DIGEST` 非空时，先做非破坏性探针
   `sudo -n "$PRIV" write-digest --digest ""`（支持时打 `STP_WRITE_DIGEST_SKIPPED` 且 exit 0；
   旧 wrapper 走 argparse exit 2）。缺失能力 → 打 `ERROR: ... outdated wrapper; run
   tools/ansible/playbooks/update_agent.yml ...` → exit 1——在**任何主机变更之前**拦下，
   不再产出「已部署却记失败」的半态，也不再让失败落在尾部；
2. **失败 message 纳入 stderr**：`_remote_failure_message(stdout, stderr, exit_code)`，
   顺序 = stderr `STP_AGENT_PRIV_ERROR:` → stdout `ERROR:` → stderr 末行（argparse 的
   `error: argument ...` 在末行）→ 通用文案。

不改的部分：热更新载荷仍不含 wrapper——wrapper 在安装目录外、root 所有，由 install/Ansible
轨道交付（ADR-0037）；本单只让控制面**感知**这一前置条件并把失败前移。

## Alternatives

- **让旧 wrapper 走 legacy `sudo tee` 写 digest**：被否。wrapper 主机的 sudoers 面已收窄为
  wrapper 单命令 + 固定 systemctl（wrapper 迁移的既定方向），回退到 `tee` 等于要求宽规则
  常驻；且真正的修复动作是升级 wrapper，不是绕过能力缺失。
- **保持尾部失败、只补 message**：被否。14 台已「部署成功但记为失败」，且 digest 缺失使
  no-op gate 永不收敛 → 每次批量都重复全量部署并再次失败（复发而非一次性）。
- **通用 capability 子命令**（wrapper 打印支持列表）：旧 wrapper 同样不支持该子命令，
  仍需探针兜底；当前只有 digest 一处新增能力，按需探针是最小面。若后续再增子命令
  （如 ADR-0040 D3 的 `host-resources` 通道），应升级为显式能力清单。
- **把 digest 写盘挪到 restart 前**（顺手消除尾部失败）：超出本单，且涉及 ADR-0040 D2
  「探活通过才写」的语义与回滚设计——另立 #1943。

## Verification

- `pytest backend/tests/services/test_host_updater.py`：28 passed（新增 3：stderr 哨兵
  优先、argparse 末行回落、脚本含能力探针与提前 exit；改写 1：digest 写入仍在探活后、
  以完整调用串定位避免与探针串混淆）；
- bash 级控制流验证（临时 harness，从生成脚本切出探针段，stub wrapper 两分支）：
  旧 wrapper（exit 2）→ `ERROR: ... outdated wrapper ...` + exit 1，不触达后续动作；
  新 wrapper（exit 0）→ 继续执行；
- 生产验证（2026-09-14）：
  - `ansible-playbook playbooks/update_agent.yml --limit <14 台>`（04:33-04:35 CST）
    → wrapper 归位 `a6335984`，`write-digest --digest ""` 实机返回 `STP_WRITE_DIGEST_SKIPPED`；
  - 重跑热更新 14 台 → **14/14 ok**（`priv=wrapper`，message `OK: service restarted
    successfully`），`agent_code_deployed` 刷新为 `ae77ca90`，`ARTIFACT_DIGEST` 落盘
    `sha256:0fe2563f...`（= desired），审计 14 条 `ok=true`；
- `python scripts/run_gates.py check:quick`：见 PR 描述。

## Revisit

- 若后续 wrapper 再增子命令，把「按需探针」升级为显式 capability 清单（一次性协商、
  脚本开头输出 `STP_PRIV_CAPABILITIES=...`），避免逐个 `if !` 探针堆积；
- #1943 落地后，本单的探针仍保留：它拦截的是「wrapper 版本落后」这一安装态前置条件，
  与 digest 写盘时机无关；
- 旧 wrapper 的存量面：本单后仍会以 exit 1 明确拒绝（而非静默降级），若批量命中多台，
  应把「wrapper 版本」纳入控制面 host 视图（当前只在 `priv_mode` 哨兵中间接可见）。
