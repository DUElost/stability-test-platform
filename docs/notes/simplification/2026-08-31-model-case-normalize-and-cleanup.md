# #644 收尾清理：型号大小写归一 + 单项目聚合收窄 + 陈旧注释清理

日期：2026-08-31 · 类型：simplification · 关联：#644 复核 · PR：#661

## 决定了什么

1. **映射写入统一大写**（P2 大小写不对称）：`_normalize_models` 加
   strip + upper。唯一索引按 `lower(match_value)` 防双归属，但 join 是
   全等——大小写不一致会让「项目卡片显示已映射、inventory 显示未映射」
   两边同时正确。生产设备 model 事实全大写（getprop 上报），写入大写
   即与事实对齐。若未来设备侧出现小写型号需 join 归一（另案）。
2. **`_summary_rows_for(db, pids)`**：单项目路径（详情/归档/解档/创建后
   返回）不再跑全表 join 聚合再取单行（反向 N+1）；列表页仍走全量版。
3. **缩进错位 8 处 + 陈旧注释 5 处**：删 match_type 行残留的 24 空格
   缩进（projects.py ×3 + devices.py ×2 等）；attribution_source 旧三态
   docstring（rule/manual/unassigned → mapped/unmapped）、
   `device.project_id` 引用类注释全部改为现状口径（「文档只写现状」）。
   migration 文件是历史记录，不改。

## 放弃的备选

- **join 归一**（`lower(Device.model) = lower(match_value)` 全路径）：
  彻底但改动 6+ 读路径 + 全部相关测试，风险与收益不成比例——生产设备
  事实全大写，写入归一已消除不对称的根源
- **前端 datalist 建议统一**：无必要——后端已归一

## 如何验证

- `TestNormalizeModelsCase` 2 例（preview 归一 + apply 落库大写）+ 全
  文件 63 例；devices 21 例；ruff 干净
- 行为变化面：map preview/apply 的入参大小写不敏感（此前大小写敏感）；
  详情返回的聚合口径不变（同 SQL 加过滤）

## 何时重议

- 设备 model 事实出现大小写混用 → join 归一（另案）
