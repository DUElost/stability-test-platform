# Plan specialty_key 可选化：普通编辑 422 与链尾追加对齐（#882/#778）

Status: implemented
Class: bug-fix

## Decision

修复「编辑 Plan 不改专项即 422」（#882，R01-F02）并顺带对齐链尾路径（#778
同源缺陷，按 #882 验收要求一并收口）：

1. **`PlanUpdate.specialty_key: str` → `Optional[str] = None`**：处理函数
   （`update_plan`）本就按 fields_set 实现局部更新（`if "specialty_key" in
   fields_set`），必填声明与实现脱节——前端「专项不变即省略」（#405 审计
   纪律）的请求在进入更新函数前 422。改可选后：省略 = 不变；显式 null =
   解绑（D2/D6「不限」，与 project_key 完全一致）。仅改 schema 一行 +
   更新过时注释（原「schema 强制，无清除语义」）。
2. **`PlanChainTailCreate.specialty_key` 可选 + 缺省继承**（#778）：前端链尾
   payload 从不携带归属字段（usePlanEditForm.ts:326-344），必填即每次追加
   必 422。缺省时**继承链尾 Plan 的专项**（链条延续语义，由
   `append_chain_tail` 实现）；显式提供仍按值解析（404 保护不变）。
3. 前端零改动：保存路径本就按变更发送；链尾 TS 类型本就无该字段——两处
   行为与后端新契约天然一致。

## Alternatives

- **前端永远带上当前专项**（issue 备选 2）——放弃：违反 #405「变更才进
  payload」审计纪律，每次无关保存都会在审计里记归属变更；
- **链尾缺省 = 显式「不限」**——放弃：链尾新 Plan 与链尾 Plan 是同一条执行
  链的延续，继承专项比落到「不限」更符合编排语义；显式传值仍可覆盖；
- **只修 PUT 不动链尾**——放弃：#882 验收明确要求「与 #778 链尾路径契约
  对齐」，且两者是同一处 schema 漂移的两个表面。

## Verification

- `backend/tests/api/test_plans_api.py` **65 passed**（新增 5 用例：省略
  specialty_key 改名/改步骤成功且专项不变、显式 null 解绑、改专项生效、
  链尾缺省继承链尾专项、链尾显式专项生效）；
- `check:quick` 7 门禁全绿。

## Revisit

- #778 曾建议「payload + TS 类型补 specialty_key」——本实现选择后端缺省
  继承，前端类型保持不带该字段；若未来前端需要显式指定链尾专项，TS 类型
  增补 `specialty_key?: string | null` 即可（后端已接受）；
- PlanCreate 的 specialty 必填（创建场景 ADR-0029 P1-B）未动。
