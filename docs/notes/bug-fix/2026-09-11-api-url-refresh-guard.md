# API_URL 回写不得用空值覆盖目标机（#1250 迁移暴露）

Status: implemented
Class: bug-fix

## Decision

根因（2026-09-11 fleet 迁移实测）：`update_agent.yml` 的
`Update API_URL in deployed environment file` 任务**无条件**用
`agent_api_url`（group_vars 默认 `""`）覆盖目标机 `.env` 的 `API_URL=`。
首轮 canary（未带 `-e agent_api_url=`）因此把主机心跳 URL 写成空串：
agent 重启后 `Invalid URL '/api/v1/heartbeat'`、`agentctl health` 判
「服务器连接: 未配置」rc=1 → 升级回滚（回滚不覆盖 `.env`），主机处于
不回连状态，须带 `-e` 重跑才恢复。属 pre-existing 缺陷，#1250 迁移把它
暴露出来。

修复：回写改用 pre_tasks 已解析的 `agent_upgrade_api_url`（#1250 引入，
优先级 = `-e agent_api_url=` 显式覆盖 > 目标机 `.env` 现值），空值场景由
同一解析链的 assert 提前拦截。语义变化：未显式覆盖时回写为 no-op
（保留目标机现值），显式覆盖行为不变。

## Alternatives

- **给任务加 `when: agent_api_url | length > 0`**：能防空写，但「不传就
  不同步」把「地址已变更」场景也挡掉；用已解析值同时覆盖两类场景，更小；
- **解析为空时直接 fail**：已在 #1250 的
  `Assert upgrade gate target is resolvable` 中实现；本单只堵回写路径；
- **保持现状（文档要求始终带 `-e`）**：放弃——runbook/README 的主流示例
  均不带 `-e`，依赖文档纪律挡不住「空值覆盖生产配置」。

## Verification

实际运行（本次 fleet 迁移即回归现场）：

- 首轮 canary（无 `-e`）复现：`API_URL=` 空 → `agentctl health` rc=1 →
  回滚；带 `-e agent_api_url=http://172.21.x.x` 重跑 →
  `health_rc=0`、`failed=0`；
- 全 14 台迁移后核验：`wrapper_rc=0`、wrapper sudoers 规则在、宽规则
  全 0、`API_URL=http://172.21.x.x`、`agentctl health` 服务 active +
  服务器连接正常；
- `pytest tests/test_update_agent_playbook.py -q` → **9 passed**（新增
  `test_api_url_refresh_never_writes_empty_override`：必须用已解析值、
  禁止裸 `agent_api_url`、解析任务名在位）。

未完成（pending）：

- 本次修复合入后，下一次 `update_agent.yml` 运行才携带该行为；在合入前
  的迁移运行必须继续带 `-e agent_api_url=`。

## Revisit

- 若未来控制面地址需要按主机差异化，`-e` 覆盖只能全局一份——届时再评估
  inventory 级 `agent_api_url` 主机变量（当前 fleet 统一为
  `http://172.21.x.x`，不做提前设计）；
- 空地址以外的弱值（如占位符）未做校验：如出现此类错误地址，同样由
  `agentctl health` 的服务器连接检查兜底（回滚 + 报错），暂不加额外解析规则。
