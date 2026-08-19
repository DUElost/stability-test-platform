# ADR-0029 P1 落地：test_project 建表与 M-b/M-c 回填

Status: implemented
Class: feature

## 决定了什么

- **P1 M-a**：`test_project`（v2 最小形态：`project_key` / `display_name` /
  `jira_project_key` + facet 四列 + `status`；**不含** `variables`（D4 挂起）与
  `storage_key`（D7 挂起））+ `specialty` 字典表（D6）；归属列 `plan.project_id` /
  `plan.specialty_id`、`device.project_id`、`plan_run.project_id` / `build_version`
  全部 nullable。migration `r5s6t7u8v9w0`（head `q4r5s6t7u8v9` → 新）。
- **P1 M-b/M-c**：回填脚本 `tools/dev/backfill-test-project.py`
  （`--phase mb|mc|all --dry-run`），规则与 ADR-0029 §迁移与回滚逐条对齐：
  - 项目行 / 字典种子在**脚本**建（migration 纯 DDL）——建行是数据不是结构，
    且要幂等重跑，脚本里按 `project_key` upsert 比 migration 更可控；
  - 回填以「目标列为 NULL」为条件，重跑不覆盖已确认归属；
  - M-c 的 model → 项目映射 = §5 人工确认清单（2026-08-18），**不自动推断**；
  - **清单外 model 拒绝执行**（exit 2，dry-run 也显式提示）——未知 model 不能
    推断归属，宁可卡住也不漏划（漏划 = 静默错划到 LEGACY，M-c 完成标准会假归零）；
  - M-c 完成标准内建于脚本：回填后 `device.project_id` 无 NULL 才收尾，否则 exit 2。
- `jira_project_key` 全留 NULL：§5 只有「MLD → STABILITY-A」是 R4 的**举例**，
  不是已确认的外部映射值，P3 填齐。

## 放弃的备选

- **migration 里插种子行**：`op.bulk_insert` 把数据绑死在 DDL 上，幂等语义要
  migration 自己写；脚本化后 dry-run 可见、可分批、可回滚，职责也更干净。
- **M-c 按 model 前缀自动推断**（如 `startswith('MLD')`）：与「不自动推断」原则
  直接冲突，且未来的新 model 会被静默归入错误项目；显式清单 + 拒绝是安全的。
- **unknown model 静默归 LEGACY**：会让完成标准「NULL 归零」假阳性——未知机型
  进 LEGACY 后再无机制发现，等 P2 前端盘点时才暴露错划。
- **`model IS NULL → LEGACY`（审查阻断项，2026-08-19 修复）**：初版把「model 空」
  当作未识别设备的判据自动归 LEGACY——但这同样是推断，且踩活设备：id 2/11
  （A2WENX 前缀）当天仍在心跳，只是 ADB 报错读不出 model，恢复后会上报真实机型；
  幂等条件 `WHERE project_id IS NULL` 使归属一旦写入永不复评，会被永久封死在
  LEGACY。修复：**6 台按 serial 显式写进 `UNASSIGNED_SERIALS`**（§5 人工确认的
  就是这一批具体设备），其余任何 model 空设备（新机上架未上报 / ADB 故障）按
  「清单外」处理 → exit 2——未来触发中断而非静默封存。

## 如何验证

- 隔离 PG（Docker postgres:16，testcontainers 同款路径）：全链
  `alembic upgrade head`（含新 revision）→ 结构断言 → `downgrade` 回滚（窗口 A）→
  再 upgrade。
- 样例数据（2 Plan / 3 PlanRun / 9 Device 覆盖全部族 + 1 空 model）：
  M-b dry-run 出计划 → 执行 → 幂等重跑 0 动作；M-c dry-run 族清单 → 执行 →
  NULL 归零 → 幂等重跑 0 台；未知 model（MYSTERY_X）dry-run 提示、执行拒绝 exit 2。
- **生产执行**：本机 PG 即生产库——迁移与回填不在此处试跑；部署时由运维流程
  （`alembic upgrade head` + 脚本先 `--dry-run` 核对清单再执行）完成。
- 审查修复重验证（隔离 PG）：清单内 serial + model 空 → LEGACY；model 空但
  serial 不在清单 → ✗ 拒绝；未知 model → ✗ 拒绝（dry-run 提示、执行 exit 2）；
  正常族归位 + 幂等重跑 0 台 + 完成标准归零全绿。

## 何时重议

- 生产库执行后，CLAUDE.md / ADR 决策表行的「落地 P1」标记从「进行中」翻「完成」。
- D1 复议（params_override 激活）时：`plan_run` 快照要补解析后字面值，
  届时 `build_version` 列与此共享迁移头，注意避让。
- M-c 清单新增族（新 project_key）：先更新 `MODEL_TO_PROJECT` 再跑脚本，
  未知 model 拒绝机制会先拦下一批未登记机型。
- 新机「未识别」需归 LEGACY：须**人工确认** serial 后补入 `UNASSIGNED_SERIALS`，
  不能把「model 空」当判据（审查修复的结论）。
