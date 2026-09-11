# Agent 主日志轮转保障（#1265 / R14-F19）

Status: implemented
Class: bug-fix

## Decision

本质问题：unit 以 `StandardOutput=append:` / `StandardError=append:` 持续写
`<install_dir>/logs/agent.log` 与 `agent_error.log`
（`backend/agent/stability-test-agent.service:41`），systemd 自身不做轮转；
LogArchiver 只处理任务运行目录——主机无额外 logrotate 时长期运行会耗尽系统盘。

修复 = **安装链落盘 logrotate 配置 + runbook 运维要求**：

- `install_agent.sh` 第 8.5 步生成 `/etc/logrotate.d/<service>`：
  两个日志路径 + `size 50M`、`rotate 5`、`compress` / `delaycompress`、
  `missingok`、`notifempty`、`copytruncate`（copytruncate 适配 systemd
  持续 append 的写句柄，无需信号重开服务）；
- `/etc/logrotate.d` 不存在时 warn 并指向 runbook（fail-soft，不阻断安装）；
- runbook §5 install 章节：职责登记 + 运维要求（确认 logrotate 在运行、对
  `<install_dir>/logs` 所在分区配置容量告警、主机全局策略叠加注意）。

容量告警（验收第 2 条）取"文档运维要求"路径：HddSpillMonitor 面向 AEE 数据盘
（HDD spill 机制），不覆盖系统盘日志目录——文档明确要求对日志分区做容量监控。

## Alternatives

- **改用 systemd journal（去掉 append:）**——放弃：Agent 主机 journal 容量同样
  需要 journald 限制配置，且会改变日志读取路径（agentctl / check 直接 tail 文件）；
- **应用层自行轮转（Agent 进程内）**——放弃：主日志由 systemd 以 append 直写，
  进程内无法安全截断；logrotate `copytruncate` 是标准模式（控制面 `backend.log`
  已采用同一模式）；
- **把 logrotate 配置交给 Ansible update 链刷新**——放弃（本批）：配置内容仅依赖
  安装路径（install 期已确定），update 无刷新必要；未来参数演进再挂 update 检测。

## Verification

实际运行（worktree `/tmp/stp-1265`，基于 `origin/main`）：

- `pytest tests/test_install_agent_artifacts.py -q` → **12 passed**（新增 1 例：
  unit append 目标 ↔ logrotate 覆盖的跨源断言 + 大小/保留/截断参数断言）；
- **反向验证**：暂存 `install_agent.sh`（回退到无 logrotate 段）→ 新测试
  1 failed；恢复后 12 passed；
- `bash -n backend/agent/install_agent.sh` → 通过；
- `check:quick` → 7 gates 全绿。

未完成（pending）：

- 真机验证（隔离 VM：干净安装后确认 `/etc/logrotate.d/<service>` 内容并
  `logrotate -d` 干跑）：本机为生产控制面宿主，不做破坏性安装。

## Revisit

- 若 Agent 主日志量级增长（高频日志），评估收紧 size / rotate 或转 journald；
- 安装目录可配（非 /opt）时 logrotate 内容随 `${INSTALL_DIR}` 自动正确
  （unquoted heredoc 展开）。
