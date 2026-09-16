# Agent 安装被作业窗口取消却报成「安装失败」（#2220）

Status: implemented
Class: bug-fix

## Decision

S5 把 RunConsole 的 `CANCELED` 与 `FAILED` 分开报，并给出可执行的复跑线索：

- 新增失败码 `agent_install_canceled`（`tools/site_config/checks.py` 的 MESSAGES 里带完整
  remediation：说明「不是脚本失败」、有界窗口（SAQ 900s）、复跑方式、以及反复被取消时的
  绕过——目标机预装大件）；
- `await_install`（`tools/site_config/agents.py`）**先判 CANCELED 再判 saq**：现场实测作业窗口
  到期时 SAQ 侧同时是 `failed`/`aborted`，原先的合并条件（`console in {FAILED, CANCELED} or
  saq in {failed, aborted}`）会把它记成 `agent_install_failed`；
- check 的 message 带上判定依据与 RunConsole 日志路径：`console=CANCELED, saq=aborted,
  log=<path>` —— 操作者可以直接去看卡在哪一步，不用再猜。

为什么用「取消」而不是「超时」命名：RunConsole 的 `CANCELED` 同时覆盖显式取消（cancel API）
与作业窗口到期（进程组被信号终止 → rc -15/-9 → CANCELED，见 `run_console.py`）。命名成超时会
把显式取消说成超时；文案里把两种可能都讲清楚，并优先给出「先看日志再复跑」的动作。

## 后续（同日）

本单只解决**读数**：把 CANCELED 与 FAILED 分开报。同日 owner 裁决根因（安装被 SAQ 作业窗口
持有）并立 [ADR-0044](../../adr/ADR-0044-agent-install-execution-ownership.md)：Agent 安装改由
RunConsole 自持、删除等待它的 SAQ 作业，本单的判据保留并加一条「记录已丢失（`lost`）按取消报」。
故本文提到的「900s 窗口」在 ADR-0044 落地后不再存在——慢目标机的正确提示回到
`install_timeout`（先预装大件）。

## Alternatives

- **在 S5 里判「是不是 SAQ 作业窗口」**：`/install/status` 只暴露 saq status 与 console status，
  没有作业开始时间/超时值；要精确判定得改后端接口或读 SAQ 存储——超出「最小」范围，且判错会
  把显式取消误报成超时。改为文案里列出两种可能。
- **`saq == "aborted"` 且没有 console 时也报 canceled**：SAQ 的 `aborted` 确实偏取消语义，但这条
  分支现场未观测到；改动它属于猜。保留原映射（`agent_install_failed`），留待观测到再动。
- **直接把 SAQ 作业窗口调大**（例如 900s → 3600s）：治的是症状——慢的目标机只会把问题推迟，
  而且窗口越长，卡住的作业占 worker 越久。窗口可配与「作业只负责 start、进度由 console 自持」
  是 #2220 里记录的根治候选，需要单独裁决（涉及 worker 占用与取消语义）。
- **把 `install_timeout`（S5 自己的轮询上限）也合并进来**：两者语义不同（被动取消 vs 主动放弃），
  合并会让操作者分不清是「跑太久被我放弃」还是「被别人掐了」。

## Verification

- `tests/test_site_agents.py` **80 passed**，新增/加固 3 条：
  - `test_canceled_install_is_reported_as_canceled_not_failed`：构造 `console=CANCELED` +
    `saq=aborted` + `log_path`，断言码是 `agent_install_canceled`、**不出现** `agent_install_failed`、
    message 含 `CANCELED`/`saq=aborted`/日志路径、remediation 含 `900s` 与 `deploy/agent/install.sh`；
  - `test_script_failure_still_reports_failed`：`saq=failed` + `console=None` 仍报
    `agent_install_failed`（防上面的分支吃掉真失败）；
  - 既有 `test_install_run_failure_is_reported`（console FAILED）与 `test_install_timeout_is_reported`
    原样通过。
  - 对新增分支做了**变异反查**：把 `if console in {"CANCELED"}` 改成恒假后，取消用例如期红。
- `tests/` 全量 + `ruff` + `scripts/run_gates.py check:quick` 结果见 PR。
- `docs/operations/installation.md` 的 Fix 表新增 `agent_install_canceled` 行，明确标注
  「预装大件」是**绕过**、终态出口是 #2220。

## Revisit

- **窗口可配 / 作业与 console 生命周期解耦**（#2220 的根治项）：当前 900s 是
  `backend/api/routes/hosts.py` 里写死的；慢镜像或首次装大件必踩。要做就得同时想清楚 worker
  占用与取消语义（超时只是取消的一种来源）。
- **双 ansible 进程观察**：现场曾看到同一主机同时有两个 `ansible-playbook`（一个来自 console、
  一个时间点晚 10 分钟），当时未能定位；本次修复不涉及它，但若再次出现应查明是否存在第二条
  执行路径（#2220 里留了记录）。
- **`saq=aborted` 且无 console** 的映射仍是 `agent_install_failed`：现场未观测，先不动。
