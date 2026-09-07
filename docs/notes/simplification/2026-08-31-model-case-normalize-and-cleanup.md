# #644 收尾清理：型号大小写归一 + 单项目聚合收窄 + 陈旧注释清理

日期：2026-08-31 · 类型：simplification · 关联：#644 复核 · PR：#661

> **同日夜已被部分推翻（`0b5804e0`，PR #675）**：写端由 PR #661 的「统一大写」
> 回归为**读端 `lower()` 归一匹配 + 写设备事实原值 + IntegrityError→409 兜底**。
> 本文「决定 1 / 放弃的备选 / 如何验证」中关于写端大写的表述以
> [`2026-08-31-map-apply-case-roundtrip.md`](../bug-fix/2026-08-31-map-apply-case-roundtrip.md)
> 记载的终态为准。

## 决定了什么

1. **映射写成员行用设备事实原值 + 读端 `lower()` 归一匹配**（P2 大小写
   不对称，终态 = PR #675 `0b5804e0` 回归）：唯一索引按
   `lower(match_value)` 防双归属；join 全等会让混合大小写设备事实
   （生产实证 `Infinix_X1102D`）在写端归一后读端 miss、映射路径锁死
   （HTTP 500）——故读端改 `func.lower` 归一命中、apply 写 `model_facts`
   原值（join 全等命中），并发双写由 flush 后 IntegrityError→409 兜底。
   原 PR #661「写端统一大写」同日被推翻（见
   [`2026-08-31-map-apply-case-roundtrip.md`](../bug-fix/2026-08-31-map-apply-case-roundtrip.md)）。
2. **`_summary_rows_for(db, pids)`**：单项目路径（详情/归档/解档/创建后
   返回）不再跑全表 join 聚合再取单行（反向 N+1）；列表页仍走全量版。
3. **缩进错位 8 处 + 陈旧注释 5 处**：删 match_type 行残留的 24 空格
   缩进（projects.py ×3 + devices.py ×2 等）；attribution_source 旧三态
   docstring（rule/manual/unassigned → mapped/unmapped）、
   `device.project_id` 引用类注释全部改为现状口径（「文档只写现状」）。
   migration 文件是历史记录，不改。

## 放弃的备选

- **join 归一全路径**（所有读路径 `lower(Device.model) = lower(match_value)`）：
  原判彻底但改动 6+ 读路径 + 全部相关测试——PR #675 已在 map/apply 读端
  落地 `lower()` 归一 + 写设备事实原值，主路径不对称已消除，全路径归一
  不再需要（残余读路径维持现状口径）
- **前端 datalist 建议统一**：无必要——后端已归一

## 如何验证

- `TestNormalizeModelsCase` 2 例（preview/读端归一 + apply 落库设备事实
  原值；#675 回归后）+ 全文件 63 例；devices 21 例；ruff 干净
- 行为变化面：map preview/apply 的入参大小写不敏感（此前大小写敏感）；
  详情返回的聚合口径不变（同 SQL 加过滤）

## 何时重议

- 出现 `lower()` 归一仍不能解决的型号等价问题（如全角/半角、分隔符差异）
  → 复议全路径 join 归一（另案）
