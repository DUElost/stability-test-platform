# 表单自动填充扫尾·残余两个：WifiPage 与设置页提交改以 DOM 值为准（#2456）

Status: implemented
Class: bug-fix

- 日期：2026-09-17
- 相关：`#2456`（本单重开后的残余）、`#2461`（前一批 4 个表单 + 共享助手）、`#2497`（同现场的另一条：受控回写导致页面卡死）

## Decision

`#2461` 交付 4 个表单时，把这两个**明确留了出口**（理由见其 Note）；本单按出口收口：

### 1. `WifiPage`：提交载荷改由 DOM 值构造（mutation 入参签名随之调整）

- 六个字段补 `name`（`name`/`config_ssid`/`config_password`/`config_router_ip`/`max_devices`/`host_group`）；
- 新增 `buildPayload(values)`；两个 mutation 由「闭包读 state」改为**接收 values**：
  `createMutation.mutate(values)` / `updateMutation.mutate({ id, values })`；
- `maxDevicesValue()` → `maxDevicesFrom(raw: string)`：同一套钳制逻辑（空/非法→默认，其余夹到
  `[1, MAX_DEVICES_LIMIT]`），但作用于**提交那一刻读到的值**而不是 state。

### 2. `AiAssistantSettingsPage`：无 `<form>`，用 `document` 作读取根 + 数值防御性转换

- 七个标量字段补 `name`（`ai_base_url`/`ai_api_key`/`ai_model`/`ai_temperature`/`ai_max_turns`/
  `ai_max_auto_continuations`/`ai_request_timeout_seconds`）；
- 本页**没有 `<form>` 元素**，故 `readNamedValues(document, {...})`——字段名在本页唯一，
  不必为 `ref` 改结构（`readNamedValues` 的 `root` 参数本就是 `ParentNode`，`document` 同样成立）；
- 数值字段由 DOM 字符串经 `toNumber(raw, fallback)` 还原（输入框是 `type=number`，正常路径等价；
  非数值回落原 state，不引入 NaN）。

**为什么本页值得改**：`api_key` 的语义是「**留空 = 不变更**」。管理器直写 `.value` 而 React 收不到
change 时，state 里仍是空 → 用户刚填的 Key 被**静默丢弃**（保存成功、Key 没变），比报错更难发现。

**不动**：复选框（`ai-enabled`/`ai-t1-confirm`）与 T2b 白名单列表编辑器仍由 state 驱动——
前者不是自动填充目标，后者是对象数组的行编辑（不是「一次填完提交」的形态）。

## Alternatives

- **WifiPage：只把 state 同步后再 mutate**：`setForm(...)` 是异步的，`mutate()` 同帧读到的仍是旧值
  （除非改成 `useEffect` 驱动，复杂度更高）。否。
- **设置页：把字段包进 `<form>` 或用容器 `ref`**：前者要动布局结构（`PageContainer` 的子元素约定），
  后者要求先找到同时包住两张 Card 的节点——而 `document` 在「字段名本页唯一」的前提下等价且零结构改动。否。
- **两页都改非受控（照搬 `#2497`）**：`#2456` 的口径是**提交取值**；`#2497` 的「不回写管理器填值」
  是另一层（且只对「填充后卡死」的那一处做过验证）。混做会让两层的回归网纠缠。否。

## Verification

- `WifiPage.test.tsx` → **3 passed**（新增：管理器直写 + 非冒泡 `input` → 提交后
  `resourcePools.create` 收到的是 DOM 里的名称/SSID/密码）；
- `AiAssistantSettingsPage.test.tsx` → **9 passed**（新增：管理器填充 API Key → 保存时**随 payload 上送**，
  不再被当成「留空=不变更」）；
- **红向反证**：两页各自退回「只认 state」→ 上述两条用例**红**（2 failed / 10 passed）；还原即绿（12 passed）；
- `./node_modules/.bin/vitest run --root "$PWD"`（全量）→ **972 passed / 122 files，零失败**；
  `tsc --noEmit` 无输出；`run_gates check:quick` → **10 gates 绿**。

## Revisit

- **`document` 作为读取根的前提**：字段名（`ai_*`）在当前页面唯一。若将来另一个页面也用同名
  `name`，必须改为容器 `ref` 作用域——否则会读到别的页面的输入框。
- **T2b 白名单行**：仍由 state 驱动（对象数组，不是自动填充目标）；若真出现「管理器填了行内字段」，
  需要按行 `data-index` 定位后读 DOM，属另一处改动。
- **本单不覆盖「卡死」**：受控回写导致的页面无响应由 `#2497` 收口（改非受控）；两单的判据不同——
  出现新症状时先按「卡顿发生在请求之前？」区分属于哪一层。
