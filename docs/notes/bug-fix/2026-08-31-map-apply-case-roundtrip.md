# map/apply 大小写让位回归修复（#644 归一化只归一了一半）

日期：2026-08-31 · 类型：bug-fix · 关联：#644 大小写归一（PR #662 引入）· PR：#675

## 发生了什么

`_normalize_models` 统一 `.upper()`（#662）后，混合大小写设备事实
（生产唯一：`Infinix_X1102D`，3 台）的映射路径被锁死，HTTP 500：

1. UI 勾选设备事实 `Infinix_X1102D` → 归一 `INFINIX_X1102D`
2. `_map_preview` 设备查询 `Device.model.in_(["INFINIX_X1102D"])` 全等
   miss → 设备算 unknown、will_assign=0
3. `apply_project_map` existing 查找 `match_value == "INFINIX_X1102D"`
   全等 miss → existing=None → 不置旧行让位
4. INSERT 大写新行 → `uq_project_model_active`（lower 索引）UniqueViolation
   → 未捕获 → 500

三处合流：**写端归一、读端全等、让位不匹配**——恰把唯一混合大小写型号的
映射锁死。

## 修复（#644 用户核对报告建议 a+c）

- **读端归一匹配**：`_map_preview` 设备查询改
  `func.lower(Device.model).in_([n.lower() ...])`；unknown 判定改 lower 集合
- **写设备事实原值**：`_map_preview` 返回 `model_facts`（归一值 → 设备
  实际 model），apply 写成员行用原值——join 全等才命中设备（纯大写型号
  原值=归一值，行为不变；无设备的新型号回退归一值）
- **existing 让位归一匹配**：`func.lower(match_value) == func.lower(model)`——
  混合大小写旧行（无论原大小写）命中，让位生效
- **IntegrityError 兜底**：INSERT 后显式 `db.flush()`（立即触发约束检查），
  catch → rollback + 409（并发双写最后一道；409 非 500，用户可重试）

## 验证

- 回归测试 4 例（`TestMapApplyCaseRoundtrip`）：
  roundtrip（混合大小写设备 → 成员行原值 + 派生读 device_count 正确）、
  小写输入归一命中、SEED 让位（旧行 inactive 保留原值 + 新行原值）、
  并发 flush IntegrityError → 409
- 全量：project_routes 67 + devices/dispatcher 21 = 88 通过；ruff 干净
- 生产反例（用户实测已回滚）：修复前 500 / 修复后应 200

## 何时重议

- 设备事实出现**同型号两种大小写并存**（如一台 Infinix_X1102D + 一台
  infinix_x1102d）→ model_facts 取首个，第二台 join miss——需设备侧采集
  归一（另案）
