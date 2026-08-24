# ADR-0030 P1b 套件绑定门禁：prepare 冻结 + mtbf 参数注入 + precheck 五步门禁（#404 PR-C）

Status: implemented
Class: feature

## Decision

落地 ADR-0030 v1.4 的绑定机制（`plan.suite_id` 可空外键，NULL = P0 文件真源
模式不加门禁），三块全部挂在既有结构上、不新增机制面：

1. **prepare 冻结**（`plan_dispatcher_sync._prepare_queued_plan_run`，与 #401
   的 project/build 同一函数点）：`plan.suite_id` 非空时写
   `run_context.dispatch_suite = {suite_id, suite_name, exported_sha256,
   exported_content_sha256, apk_binding, export_dir}`。实现收在
   `services/suite_binding.freeze_dispatch_suite`；套件行缺失防御性跳过
   （FK 保证存在，缺行由门禁 fail-fast，不中断 prepare）。
2. **参数自动注入**：`plan_dispatcher_core.inject_suite_params`（WiFi 注入
   先例的同款形状）在 `materialize_jobs_and_allocations` 对 action 前缀
   `script:mtbf_` 的步骤注入 `{expected_testpoint_count(=启用用例计数),
   project(=套件 export_dir)}`——注入源是**冻结的 dispatch_suite** 而非活表
   Plan（绑定事后解除不改变在途 Run 行为的快照语义）；已有用户声明值优先；
   计数在物化时点取活表——此时五步门禁已保证 库==导出==磁盘 三方一致。
   未绑定 Plan 零注入，脚本侧 env 回落保持原样。经既有 STP_STEP_PARAMS
   通道，脚本无需改动（v1.1.0+ 的 param_or_env 已解析这两个键）。
3. **precheck 五步门禁**：`suite_binding.collect_suite_gate_error` 按
   plan.suite_id join 逐项校验 missing / not_exported / content_changed /
   sha_mismatch / project_mismatch(D3b)，挂 admission 链 Phase A0
   （script_verify_failed 同层的 `_FatalAdmission("suite_verify_failed")`，
   在更慢的脚本 RPC 校验之前 fail-fast）。判定基准 = 活表套件行 + 磁盘文件
   ——冻结块只承担 D5 归因，不参与放行（重导后新 Run 即过，归因差异由
   run_context 与 setup trace 事后比对显性化）。修复路径写入错误 detail。

配套两处：

- **#402 守卫精确化**（`routes/suites.py`）：宽匹配拆成两个查询——绑定同套件
  的 ACTIVE Run 硬阻断（409 `SUITE_RUNS_ACTIVE`，**force 不豁免**）；无绑定的
  P0 存量 Run 保留宽匹配兜底（409 `ACTIVE_MTBF_RUNS`，force 可越过）。跨套件
  并发导出不再互相阻塞。软删端点兑现其 docstring 承诺，同样按绑定集合阻断。
- **suite_key 注释清零**：`models/suite.py` / `schemas/suite.py` 的旧机制注释
  改述为 name/suite_id 新机制；设计文档 §3 抬头注记与 §8 表行的 `suite_key`
  字面量改述（变迁史保留、专名隐去），issue #404 的机械判据
  `grep -rn suite_key … | grep -v tests | grep -v 修订记录` 达到 0 命中。

## 放弃的备选

- **门禁比较基准用冻结指纹**（disk sha 对比 prepare 时点的 exported_sha256）：
  拒绝。「重导即可修复」的门禁语义会被破坏——重导后磁盘已与新基线一致，
  却仍对旧冻结值报 sha_mismatch，操作者无从修复只能重新派发；归因职责交给
  冻结块自身（run_context vs setup trace 事后比对）已足够。
- **注入进 plan_snapshot（prepare 时点）而非物化时点**：拒绝。快照重建链路
  （build_lifecycle_from_snapshot）会丢掉 lifecycle 注入，必须改快照 schema；
  物化时点与 WiFi 注入同点，且此时门禁已放行、计数与磁盘状态一致。
- **D3b 用 SQL `Device.project_id != suite.project_id` 单条件**：有 NULL 陷阱
  （NULL 比较不命中 → 未归属设备静默放行），改为 `or_(is_(None), !=)` 显式
  fail-closed。
- **软删端点不动**：其 docstring 明文承诺「守卫在 P1b 落地」，留空即制造新的
  错锚；补上与 export 同款的绑定集合阻断（几行 + 一个测试）。

## 如何验证

- 新增 `backend/tests/services/test_suite_binding_gate.py` ×16：prepare 冻结
  golden（含未绑定零字段）、五步门禁矩阵各一反一正（含修复后放行）、NULL 设备
  fail-closed、通用套件放行、未绑定永不进门禁、admission 集成（fatal 于脚本
  校验之前 / 全绿准入且 job params 含注入值）、注入 golden（已有值优先 /
  非 mtbf 步骤不受影响 / 未绑定不注入）。
- `test_mtbf_suite_routes.py` 增 `TestActiveRunGuardPrecision` ×3：同套件阻断
  （force 亦 409）、跨套件放行、软删阻断；原 TestActiveRunGuard ×4（全部无绑定
  场景）不改一行仍然全绿——force 逃生阀只对存量语义收窄的回归证明。
- 回归：backend/tests 全量 **1619 passed, 16 skipped**（testcontainers PG，
  `unset TEST_DATABASE_URL`）；ruff 全仓干净。前端未触碰。
- 迁移红线遵守：PR-C 无新迁移，全程未在 backend/ 下执行 alembic。

## 边界与何时重议

- 门禁判定用活表+磁盘、冻结块只做归因——若未来要求「准入后清单不得再动」的
  强快照语义（prepare→admit 间重导也算漂移），需把第 4 步基准换成冻结值，
  届时应连同「重导须重新派发」的操作语义一起重议。
- 多套件共享同一 export_dir（如都落 legacy）时跨套件互不阻断是设计接受的
  面积——运维上应把共享目录视为误配置；需要目录级互斥时另起讨论。
- PR-D（同 issue）：`STP_MTBF_EXPECTED_TESTPOINT_COUNT` 摘出
  `_FLEET_ENV_KEYS` + mtbf-api.md §1.5 同步 + 七挂靠位复核；注意双层退役
  （控制面摘键后旧脚本回落默认 0 安全降级，新脚本版本移除读取走 ADR-0020
  版本化流程）。`suite_unbound` WARNING 与硬拒翻转安排见 #404 评论第三步。
- P1c（CLI + mtbf-api §2 + 05-data-model + ADR 状态传播）另行推进。
