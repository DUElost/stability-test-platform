# 热更新服务就绪门禁：未 active 即失败（#1253）

Status: implemented
Class: bug-fix

## Decision

#1253（R14-F07）：热更新远程脚本在 `systemctl is-active` 失败时只打 WARN、
不返回非零；`execute_hot_update` 把 exit 0 映射为 `ok=True` 并可能记录部署修订
——systemd 接受重启但新进程立即崩溃时仍宣告成功。

修复（远程脚本内收口）：

- WARN 分支移除；改为**重试窗口**：重启后 5 次 × 1s 探测 `is-active`
  （覆盖慢启动；原先固定 sleep 2 只探一次），窗口耗尽仍不 active → 打
  `systemctl status` 摘要（供诊断）→ 最后一行 `ERROR: ...` → `exit 1`；
- `execute_hot_update` 失败分支的 message 新增 `_remote_failure_message`：
  优先提取脚本打的 `ERROR:` 行（服务未 active / pip 失败等都有 ERROR 行），
  替换原来无法定位的 "Remote script failed (exit=1)"——API 消费方与热更新
  历史记录从此能看到原因；
- 成功路径（窗口内 active）行为不变：OK 行 + exit 0 → ok=True。

与 ADR-0033 的关系：无直接约束——这是控制面自研部署通道（Ansible/SSH 远程
脚本），不是外部工具调用；脚本退出码语义沿用既有 0/非零。

## Alternatives

- 只把 WARN 改 exit 1 不加重试：systemd `restart` 返回后服务可能仍在启动中
  （Type=simple 立即返回），单次探测会误杀慢启动——5×1s 是「不至于误报失败、
  又能抓住崩溃」的最小窗口；
- 用 `systemctl show -p SubState` / HealthCheck：依赖 unit 配置细化，收益不成
  比例；
- 心跳确认（等新 revision 上报）才判成功：最可靠但要等 Agent 回连（可能
  30s+），会把热更新 API 拉长成轮询——issue 建议「稳定运行**或** revision
  心跳确认」取前者的轻量实现；心跳确认留 Revisit。

## Verification

- `pytest backend/tests/services/test_host_updater.py`：15 passed，新增 3 例
  ——远程脚本含重试窗口与 exit 1、旧 WARN 分支移除、OK 行保留 /
  `_remote_failure_message` 优先 ERROR 行 / 无 ERROR 行回落通用文案；
- ruff 干净。

## Revisit

- 「新 revision 心跳确认」作为更强的成功判据（等待 Agent 用新 code_version
  回连心跳）——需要 Agent 与控制面协同，成本高于本单，若 WARN→exit 1 后
  仍见「active 但实际崩溃」的漏报再立项；
- 5×1s 窗口对慢宿主可能仍不足——可参数化（env → 脚本变量），出现误报再调；
- `_remote_failure_message` 只认 `ERROR:` 前缀行——远程脚本其他失败路径若打
  不同前缀，message 会回落通用文案（不致错误，只是少细节）。
