# Plan 描述清空语义修正（#1198，R12-F12）

Status: implemented
Class: bug-fix

## Decision

`handleSave` 原实现 `description: description.trim() || undefined`：清空描述（或仅
空白）时字段为 undefined，JSON 序列化省略该键；后端 `if payload.description is
not None` 跳过未设置字段——用户看到「已保存」，描述仍是旧值。反向问题同时存在：
未修改描述也会被恒发（`'旧描述' || undefined` → 恒有值）造成签名式写库。

修正（仅前端，后端零改动）：

- 从 payload 字面量移除 description；
- 更新路径：**逐字符比对原始输入**与 `plan.description`——未改动则省略（既不误写，
  也不把库中带首尾空白的值 trim 后回写）；改动则提交 `trim()` 后的值，清空提交
  **空串**（后端只跳过 None，空串才会真正落库）；
- 新建路径语义不变：空描述仍省略（后端默认 NULL）。

## Alternatives

- 恒发 `description.trim()`：未改动也写库，且把库中存的 `'  x  '` trim 回写
  （更隐蔽的误写）。
- 提交 `null` 表示清空：后端 `is not None` 直接跳过 null，接口层不支持；把后端改为
  `model_fields_set` 语义超出本单（P3）最小面。
- 引入 per-field `origDescription` 状态：与 origProjectKey 模式一致但多一份状态；
  直接用 `plan.description` 比对等价（保存成功后 `setQueryData` 已推进原值）。

## Verification

- `npx vitest run src/pages/orchestration/PlanEditPage.test.tsx` → 14/14（新增 3 例：
  清空提交空串 / 未改不携带 description / 新建语义不变）
- 红绿：未修复实现上前两例失败
- 全量前端套件 / type-check / eslint / build / `check:quick` 通过

## Revisit

- 若后端未来把 update 改为 `model_fields_set`（显式 null=清空），前端可提交 null
  表达更强语义；当前空串与 NULL 在展示上等价。
