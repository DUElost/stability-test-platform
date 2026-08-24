# export-to-tool-dir 在途守卫弱版（ADR-0030 D2 前置段，#402）

Status: implemented
Class: bug-fix

## Decision

`export_to_tool_dir`（`backend/api/routes/suites.py`）在落盘前增加 ACTIVE 守卫：
存在引用 `mtbf_*` 系脚本、状态 ∈ {QUEUED, PRECHECK, RUNNING} 的 PlanRun 时返回
409 `ACTIVE_MTBF_RUNS`（附 run id 清单，上限 50）。admin 可 `?force=true` 越过，
越过后审计 details 记 `active_guard_forced: true` + 当时在途数。

背景：P1a 管理面已上线「覆盖工具目录 runtask.xml」的能力而零拦截——MTBF 以天计
长跑，中途换清单正是 D2 明确要求拦截的场景（「存在引用该套件的 ACTIVE PlanRun
时拒绝覆盖（409）」）。本 PR 落地的是该决策的**可先行半段**；绑定字段落地后的
精确匹配属 #404。

## 放弃的备选

- **按 export_dir / 设备项目精确关联**：拒绝。P0 消费路径由 host 级 env
  （`STP_MTBF_PROJECT`）决定读哪个目录，DB 侧无从知道某个 Run 实际消费哪份文件；
  用 DB 字段做「精确」关联是虚构的精度。当前唯一不撒谎的相关性是「有 MTBF 长跑
  在飞」，此时拒绝一切工具目录覆盖。生产只有一套 legacy 部署的现状下，宽匹配
  恰好也是精确的。
- **不设 force 逃生阀**：拒绝。长跑以天计，「等它跑完再改清单」可能等一周；
  无逃生阀的守卫第一次挡住真实运维就会被整体绕过（参照 ADR-0029 备选 §10 对
  硬阻断的分析）。force 是显式动作且强制留痕，与 scripts scan
  `force_rebaseline` 同一先例。
- **守卫放在写盘后/事务里回滚**：拒绝。409 必须先于任何磁盘副作用（含 mkdir），
  测试断言了这一点。

## 如何验证

- `backend/tests/api/test_mtbf_suite_routes.py::TestActiveRunGuard` ×4：
  QUEUED 阻断 + 未落盘断言；RUNNING/PRECHECK 阻断而终态放行 + 终态化后放行；
  非 mtbf 脚本的 ACTIVE Run 不误伤；force=200 + 审计留痕。
- 既有 25 例管理面测试全绿（守卫对无在途 Run 场景零影响）；ruff 干净。

## 边界与何时重议

- 匹配键是 `PlanStep.script_name LIKE 'mtbf\_%'`（活表），依赖 ADR-0030 D1 定稿
  的三件套命名约定；若引入第四个非 mtbf_ 前缀的消费方需同步。
- #404（suite 绑定）落地时，本函数应改为按 suite 精确匹配并删除 force 的
  「等待天数」理由复核是否保留。
- 删除侧（软删 suite 时引用守卫）仍按原计划随 P1b 绑定字段落地，未在本 PR 范围。
