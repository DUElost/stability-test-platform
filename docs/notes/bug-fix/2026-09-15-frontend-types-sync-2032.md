# 前端 API 类型同步补齐 + 机器化守卫（#2032）

Status: implemented
Class: bug-fix

## Decision

**1）补齐缺失的响应字段**（`frontend/src/utils/api/types.ts`）：
- `Host.ssh_auth_type?: string | null`（后端 `HostOut:90` `Optional[str] = None`）；
- `Device.serial_suspect?: boolean`（后端 `DeviceOut:47`，由 `is_placeholder_serial` 计算后随响应下发）。

> issue 标题另提的 `HostOut.agent_artifact_digest`/`agent_resources_digest` 在先前 PR（#1929/#1966 系列）已补入前端，本轮做**逐字段比对**确认：`HostOut`/`DeviceOut` 对前端的缺口恰为上述两处。
> 该 issue 的**正文与标题不符**（正文描述的是 retention 先 rmtree 后 commit，已由 [#2033](https://github.com/DUElost/stability-test-platform/issues/2033) 独立承载）——本 PR 按标题范围修复，无信息丢失。

**2）把「硬不变量」变成机器化门禁**：新增 `tests/test_frontend_api_types_sync.py`
（纯文本解析、离线、秒级；PR 路径执行）——后端响应模型的字段名必须出现在前端对应接口里，
单向断言（前端多出的派生/兼容字段允许存在），并带两道自证：解析结果不得为空、
必须含哨兵字段 `id`；确属有意不下发的字段走 `ALLOWED_MISSING[模型]` 并注明理由。

## Alternatives

- **只补字段、不加守卫**：正是此前状态——`AGENTS.md` 写明的硬不变量没有可机器查的检查，
  才让 `ssh_auth_type` / `serial_suspect` 漏到现在（同 `2026-08-governance-surface-protection`
  的「文本在场且可机器查 → 加差异面检查」路径）→ 否决；
- **双向断言**（前端不得有后端没有的字段）：前端派生字段（如列表页拼装）是合法的，
  会让守卫频繁假红 → 采用单向；
- **从后端 schema 自动生成 types.ts**：工程量大且现有文件承载了注释与联合类型语义，
  机械生成会丢失信息 → 暂不做（若模型数量继续增长再评估）。

## Verification

- `npx tsc --noEmit` → **0 错误**；
- 新增守卫测试 **2 passed**；
- **反向验证**：临时移除本轮补的两个字段 → 守卫即刻红并**精确点名**
  `HostOut … ['ssh_auth_type']` / `DeviceOut … ['serial_suspect']`（证明它能抓住 #2032 形态）；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**；
- 影响面：**纯类型改动**，无运行时行为变更；前端现有代码未消费 `serial_suspect`
  （检索确认无重复启发式），消费留待需要时（后端字段已是权威口径）。

## Revisit

- 后端模型增删字段后，若新的响应模型也希望纳入守卫：在测试的 `MODELS` 表加一行即可；
- 若确需「有意不下发前端」的字段：进 `ALLOWED_MISSING` 并注明理由——**不得**放宽断言本身；
- 若将来前端需要在设备视图提示占位序列号：直接消费 `serial_suspect`（后端口径见
  `backend/core/device_serial.py:is_placeholder_serial`），不要在前端重复实现启发式。
