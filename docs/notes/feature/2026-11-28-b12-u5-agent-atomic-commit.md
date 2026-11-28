# B12-U5：Agent 临界区仲裁、VBUS 冻结、护盾心跳与停机 CAS

Status: implemented
Class: feature

## Decision

落地 ADR-0065 阶段 3 单元 U5（`#4206` v1.2）Agent 侧：

1. **单点互斥仲裁 + 两阶段握手客户端**：`backend/agent/critical_section.py` 的 `CriticalSectionGate` 在同一把锁上裁决 `abort`/`force` 与 `enter`；`enter` 走 U4 的 `pre_register → activate_permit`，本地余量（租约 / 宿主心跳 / `D_atomic`）不足则 deny 并 `cancel_pre_registration`。
2. **脚本 IPC（UDS）**：Unix domain socket + 行分隔 JSON（`enter/grant|deny/exit`）；环境变量 `STP_CRITICAL_SECTION_SOCK` 供 U6 刷机脚本阻塞等待 grant。通道异常 / 超时 fail-closed；grant 未递达时按 `request_id` 撤销预登记。
3. **两层冻结与步骤调度**：引擎在 init 启动时 `build_atomic_group_frozen`（静态 `atomic_group` + VBUS `return_check` 插在刷机前缀后，遵守 ADR-0063 D5.3）；普通 abort 对已 grant 组跑完必跑成员后跳过 patrol；`skipped=true` 未声明 `skip_satisfies_delivery` 判 `FAILED`；`force=true` 立即杀进程组。
4. **≤15s 护盾存活心跳**：grant 后独立线程上报；仅真实新 `PROGRESS` seq 推进 `progress_seq`；本地绝对截止与 `D_atomic` 协同。
5. **终态落盘 / 停机 CAS / 能力上报**：`/complete` update 携带 `atomic_group_*` 字段；`graceful_shutdown` 每 `job_id` CAS + SIGTERM→SIGKILL + 未核实 outbox；`AGENT_CAPABILITIES` 启用三向能力（不改控制面 `heartbeat.py`）。

### 偏离说明（IPC 协议，供 U6 消费）

- **传输**：`AF_UNIX` `SOCK_STREAM`；路径由 Agent 创建并经 `STP_CRITICAL_SECTION_SOCK` 注入子进程。
- **帧**：UTF-8 JSON 单行 + `\n`。
- **脚本 → Agent**：`{"op":"enter","request_id":"...","min_critical_section_seconds":N,"step_key":"...","step_timeout_seconds":N?}`；硬件完成后 `{"op":"exit","request_id":"..."}`。
- **Agent → 脚本**：`{"op":"grant","request_id":"...","D_atomic":epoch,"permit_id":"..."}` 或 `{"op":"deny","request_id":"...","reason":"..."}`。
- **不变量**：未收到 `grant` 前不得写硬件；deny / 超时 / 断连一律失败退出；Agent 崩溃不泄漏挂起锁（控制面 `permit_expires_at` / 欠账路径兜底）。

## Alternatives

- **stdio 旁路协议**：与现有 stdout JSON 结果契约冲突风险高，否决。
- **仅 stderr 单向打点**：ADR-0065 D3.15 明确禁止；必须同步 grant。

## Verification

```bash
./scripts/project_python.sh scripts/run_pytest.py \
  backend/agent/tests/test_b12_u5_critical_section_4206.py \
  backend/agent/tests/test_vbus_init_check_order_3892.py -q
./scripts/project_python.sh scripts/run_gates.py check:quick
```

## Revisit

- U6 刷机脚本接入 `STP_CRITICAL_SECTION_SOCK` enter/exit，并在 `capabilities.json` 声明 `critical_section_guard`。
- 若控制面 `activate_permit` 响应缺 `lease_remaining_seconds` 字段形态变化，本地余量校验需同步（不得私自改控制面）。

## 观察

- U4 握手契约可满足 C9/C10 不变量；实施中未触发 §7 退回。
- `ScriptEntry` 无 capabilities 字段，运行时从包根 `capabilities.json` 读取 `critical_section_guard`（U6 发包后生效）。
