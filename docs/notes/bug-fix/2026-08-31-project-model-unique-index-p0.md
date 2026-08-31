# uq_project_model_active 唯一索引生产丢失（P0 回归）

日期：2026-08-31 · 类型：bug-fix · 关联：ADR-0029 v2.5 · PR：#650

## 发生了什么

v2.5 部署后的生产核查发现 `project_model` 只剩主键——模型声明的
`uq_project_model_active` 部分唯一索引（`(lower(match_value)) WHERE is_active`，
「同型号不能双归属」的地基不变量）在生产库**不存在**。反例验证：插入同型号
第二个活跃成员行成功（无约束）。

## 根因链

1. `c4d5e6f7g8h9`（P1）建的是 `(match_type, lower(match_value)) WHERE is_active`
2. `f0e1d2c3b4a5`（M1 rename）只 `ALTER INDEX ... RENAME`——**列集未变**
3. `a9b8c7d6e5f4`（M3）`drop_column("project_model", "match_type")`——
   PostgreSQL **删列时静默连带删除引用该列的索引**，且未按新形态重建

「把旧机制拆掉」的那一步把新机制赖以成立的 DB 保证一起拆了。

## 为什么两道防线都没拦住

- 测试用 `Base.metadata.create_all()` 建表——schema 来自 ORM 模型（索引在），
  测试库永远有索引，无法发现 model↔migration 漂移
- `pr-migrate-empty-db` 只验「迁移能跑通」，不验「结果与模型一致」

## 修复

1. **`f8a9b0c1d2e3` 迁移**：先查活跃重复（有则 `RuntimeError` 要求人工裁决），
   再 `CREATE UNIQUE INDEX uq_project_model_active ON project_model
   (lower(match_value)) WHERE is_active`；顺手补 `device.model` 索引
   （全部归属读路径的 join 键）+ 模型 `Index` 声明同步
2. **CI 结构性补强**：`pr-migrate-empty-db` 后加一步
   `backend.scripts.check_schema_sync`——`compare_metadata` 迁移结果 vs ORM
   模型，**基线白名单**断言（diff ⊆ 基线）。全空断言在现库不成立（24+ 项
   历史噪音：FK ondelete 参数、索引命名漂移、alembic partial index 比较
   bug、modify_type 单元素 list 包装等），基线法只拦**新增**漂移，历史债
   另案收敛。`--rebaseline` 供人工确认后刷新基线
3. **`from_project_key` 下标修复**（顺手 P1）：`_map_preview` 冲突项返回
   `entry[2]`（source）应为 `entry[1]`（占用方 project_key）——API 契约
   缺陷，前端当前只渲染 serial 故不可见

## 验证

- 空库 upgrade head → 反例：重复活跃行被 `IntegrityError` 拒绝、
  inactive 重复可插（partial 语义正确）
- `check_schema_sync` 空库跑 3 次 0 NEW（key 稳定，无内存地址噪音）
- 回归：project_routes 56 + devices/mtbf_suite 51 + dispatcher 14 全过
- 生产：迁移应用后 `pg_indexes` 确认 `uq_project_model_active` 与
  `idx_device_model` 存在

## 何时重议

- 历史噪音（FK 参数、索引命名、`uq_job_active_per_device` 等）收敛时，
  基线文件应逐步缩水——每次收敛一个噪音，`--rebaseline` 一次
