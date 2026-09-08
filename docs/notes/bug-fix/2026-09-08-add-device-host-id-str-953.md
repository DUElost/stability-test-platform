# R04-F08 落地：添加设备 host_id 收字符串契约（#953）

Status: implemented
Class: bug-fix

## Decision

`Host.id` 是 `String(64)` 主键（常见 IP 派生连字符 ID 如 `192-168-1-200`、
主机名式 `host-a1`）；`AddDeviceModal` 却校验「正整数」并 `Number()` 转换
提交——非数字主机 ID 无法通过页面指定现有主机（后端 `DeviceCreate.host_id`
早已是 `Optional[str]`，表单是唯一收窄点）。

修复（前端契约对齐，`AddDeviceModal.tsx` + 类型链）：

- 校验与提交改**字符串原样传递**（格式白名单 `[A-Za-z0-9._:-]+`，空值省略
  字段；主机存在性由后端校验）；
- 输入控件 `type=number, min=1` → `type=text`，placeholder 换字符串示例；
- `DevicesPage` mutation 与 `api/devices.ts` create 的 payload 类型同步
  `host_id?: string`。

## Alternatives

- **改成主机下拉选择器（issue 建议形态）**——放弃：选择器需拉全量主机
  列表并处理跨页/容量；最小契约对齐（字符串输入 + 后端校验）已满足
  「可指定字符串 host_id」，选择器作为后续增强单独立项；
- **提交时按后端 allocate 逻辑把字符串转回派生 ID**——放弃：转换规则与
  后端 `allocate_host_id` 耦合，前端不该复刻；原样提交让后端 404 精确
  反馈。

## Verification

- **反例实证**：回退 modal + devices.ts 保留测试 → 3 用例失败（字符串
  host_id 被正整数校验拦截）；修复版全绿；
- 新增用例（`AddDeviceModal.test.tsx` +4）：`192-168-1-200` 原样提交 /
  `host-a1` 非数字可提交 / 空 host_id 省略字段 / 非法字符（含空白）被拦
  且不提交；
- `DevicesPage.test.tsx` 回归 **7 passed**；
- `check:quick`（含 eslint/tsc）与 PR 门禁：见 PR 描述。

## Revisit

- 与 #940（Agent 日志 host_id）同族不同表面：本单收 UI 表单；若后续做
  「主机选择器」，输入框路径可保留为高级模式；
- 格式白名单不限制后端合法 ID 形态（后端 String(64) 全量）——白名单外
  形态（如中文主机名）会被前端拦，属有意保守（可与后端校验扩展同步）。
