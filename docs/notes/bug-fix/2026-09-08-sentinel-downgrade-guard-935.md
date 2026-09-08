# 哨兵项目迁移 downgrade 守卫——带历史数据明确拒绝（#935）

Status: implemented
Class: bug-fix

## Decision

#935（R03-F02）：b1c2d3e4f5a6（v2.5 D11 M4）upgrade 删 GENERIC/LEGACY 哨兵且
**未保留原 id**，downgrade 重新 INSERT 同名项目得新自增 id——历史
`plan_run.project_id` 引用旧哨兵 id 时，`ADD CONSTRAINT` 全表校验必然失败，
且报错晦涩、库停在半途。

裁定采纳验收第二项（**明确拒绝**而非恢复 id 映射）：downgrade 入口加孤儿
守卫——`plan_run` 中 `project_id` 非空且引用不存在项目的行数 > 0 →
`RuntimeError("downgrade refused: …")`（含原因与恢复建议）。守卫条件覆盖
哨兵引用与任何同型孤儿（重建 FK 的前置本就是无孤儿）。空库 / 无孤儿引用的
库正常降级（往返实测成功）。

拒绝而非恢复 id 映射的理由：v2.5 派生归属重设计（device.project_id 删列改
派生、project_model 成员化等）跨多张表，降级本就不完整可逆——半途失败只会
留下更难收拾的中间态；诚实失败 + 指向备份恢复是对操作者最少的错误路径。

## Alternatives

- 显式插入旧哨兵 id（方向 A）：旧 id 不可知（upgrade 未记录），从 plan_run
  历史值反推不可靠（可能引用非哨兵行）；且即便恢复项目行，device 侧派生
  归属的逆向（重建 device.project_id 列并回填）仍无法自动完成——A 给出
  「可降级」的假象。
- 只拦哨兵引用：守卫条件泛化为任意孤儿（NOT EXISTS），一条条件覆盖全部
  ADD CONSTRAINT 前置，报错文案说明哨兵为主因。

## Verification

- `pytest backend/tests/migration/test_sentinel_downgrade_935.py`：roundtrip
  守卫测试通过（docker postgres:16 一次性容器 + 真 alembic 往返）——空库
  `upgrade head → downgrade l5m6n7o8p9q0 → upgrade head` 成功；插引用不存在
  项目 id 的历史 plan_run 后同路径降级被拒（exit≠0 + "downgrade refused"）；
  同文件既有 5 个迁移测试全过（6 passed）；
- 测试要点（留痕）：`downgrade <rev>` 不执行 rev 自身的 downgrade——目标
  必须写 `l5m6n7o8p9q0`（down_revision）；回退路径上 n4o5p6q7r8s9（#902）
  要求 `plan.specialty_id` 非空，测试数据补 specialty 种子挂引用；
- ruff、gov-surface S1–S12 全绿；
- Registry：fix-935-sentinel-downgrade-guard 全程登记（--issue 935）。

## Revisit

- 若未来需要带数据降级为受支持场景，须先补 upgrade 侧的哨兵 id 存档机制
  （如迁移专用影子表），本守卫随之移除；
- 其他「不可完整逆向」的迁移（如 a9b8c7d6e5f4 删 device project 列）是否
  需要同类守卫，可横向审计（未在本单范围）。
