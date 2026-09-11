# RunConsole 多实例边界：文档化 + 可诊断化（#1114，R11-F06）

Status: implemented
Class: bug-fix

## Decision

RunConsole 的运行记录与 `run_key` 互斥是**进程内态**：多实例（ADR-0027 opt-in）下
A 实例启动的运行在 B 无法订阅/查询/取消，且同 key 跨实例可并发（dedup Jira 同厂商
串行约束失效）。评估后按验收第 3 条分支（**文档明确单实例限制且部署强制**）落地
（用户裁决「按 B」）：

1. **ADR-0027 生产多实例检查清单增补第 6 条**：RunConsole 依赖功能（dedup Jira
   串行 / Agent 安装 console / AI 助手 console 动作与日志 / `console:` 房间订阅）
   为单实例语义——使用这些功能的部署禁止启用多实例（或先在 LB 层 sticky）。ADR 升
   **v1.2**（头部版本记录 / 修订记录 / README 主表同步；S12 校验通过）。
2. **部署强制点**：
   - 后端启动（多实例模式）输出
     `multi_instance_mode_enabled ... ref=#1114` WARN（lifespan）；
   - `environment-variables.md` 的 `STP_SOCKETIO_REDIS_ADAPTER` 行加「启用前读
     清单第 6 条」注记。
3. **可诊断化**（把困惑性失败变为可归因）：
   - 新增 `console_run_miss_hint()`：多实例下 dedup console 三处 404 详情附加
     「该运行可能由其他控制面实例持有（#1114）」；单实例下不附加（零变化）；
   - socketio `console:` 房间拒绝在多实例下留
     `console_room_refused reason=not_local_multi_instance` WARN。
   - 注：`ai_assistant.py` 取消路径已有 `CANCEL_NOT_ROUTABLE`（#1222）覆盖同类语义，
     不叠加改动。

**明确不做**：owner 路由 + 跨实例共享态（成本高、owner 失联语义复杂）；Redis
`run_key` 跨实例互斥（可选增量，本单按验收「或」分支，未采纳）。

## Alternatives

- **owner 路由 + 共享态（完整 A 方案）**：跨实例订阅/查询/取消全支持，但需 run
  registry、转发协议、owner 失联/脑裂语义与大量测试；生产当前单进程、多实例为
  opt-in，收益与成本不匹配；
- **Redis `run_key` 互斥（C）**：能给「同 key 跨实例互斥」硬保证，但给 run_console
  引入 Redis 依赖面与降级路径；待多实例下实际使用 dedup 时再立；
- **hosts 安装状态接口对 miss 增字段**：非错误路径，改 API 形状不必要，放弃。

## Verification

- 新增/扩展测试：
  - `backend/tests/services/test_run_console.py`：开关 off → 无提示/无告警；on →
    提示与告警文案含 `#1114` / `ref=#1114`；
  - `backend/tests/api/test_dedup_jira_endpoints.py`：状态 404 单实例**无**提示
    （扩展既有用例）+ 多实例**含**提示（新用例）；
- **红绿**：还原实现（`run_console.py` + `dedup.py`）→ 2 failed；修复 → **50 passed**
  （两文件）；
- `check_governance_surface.py --check` → 全绿（含 S12：ADR-0027 v1.2 头部 ↔ README
  主表 ↔ 修订记录一致）；
- `python scripts/run_gates.py check:quick` 通过。

## Revisit

- 若未来确需多实例 + console 全功能：按 owner 路由设计（run registry + 请求转发 +
  owner 失联语义）另立 ADR/需求；
- Redis 跨实例 `run_key` 互斥作为可选增量，触发条件：多实例部署下实际使用 dedup
  Jira 串行。
