# 模板墙钟装不下脚本预算：ensure_root 60→180，判据从单 action 改为全量不变式（#3087）

Status: implemented
Class: bug-fix

## Decision

`#2981` 把 check_device 的步骤墙钟从 30s 抬到 180s（脚本默认预算 150s），并留下守卫
`tests/test_check_device_template_timeout_2981.py`。**该守卫把 action 硬编码在判据里**，
于是同一窗口内 `ensure_root` 以完全相同的形态复发：版本已提到 `1.0.2`（docstring 自陈
「步骤超时必须容纳 `total_budget_seconds`（默认 150s）」），6 个种子模板的
`timeout_seconds` 却仍是 **60**。引擎按 PlanStep 墙钟 `killpg`，于是 boot 门与第二次探测
跑不到，v1.0.2 新增的 `attempts=`/`history=` 证据一行都输出不来——失败报文比旧版更差。
生产 plan 54 已用一次性 DB 事务把存量步骤抬到 180s，**仓库侧却仍会产出 60s 步骤**：
模板是新建 Plan 的唯一来源，等于把刚修好的问题重新种回去。

做两件事：

1. **6 个模板的 `ensure_root.timeout_seconds` 60 → 180**（`ddr` / `gpu` / `monkey` /
   `monkey_watcher_patrol` / `mtbf` / `standby`；180 = 预算 150 + 余量 30，与 check_device
   同口径）。`powercycle` / `sleep` 无 ensure_root 步骤，不受影响。
2. **判据改成全量不变式**（文件已 `git mv` 为
   `tests/test_template_step_timeout_vs_script_budget.py`）：遍历**全部**模板的
   `script:<name>` 步骤，解析该版本脚本声明的墙钟预算，断言
   `timeout_seconds >= 预算 + 30`。不维护可漏的名单：

   - 预算**从脚本源码解析**（`args.get("total_budget_seconds", 150)`），不抄第二份权威；
   - 没声明预算的族跳过（无可比对象）—— 但**被覆盖的族数有下限**（当前恰 2 族：
     check_device / ensure_root），正则失效时判据必须红而不是静默恒真。

   2026-09-22 实测：16 个模板步骤族里只有这 2 族声明预算，`ensure_root` 是唯一
   「墙钟 < 预算」的族 —— 与 #3087 的逐族核对结论一致。

## Alternatives

- **只改 6 个模板、守卫照旧硬编码 check_device**：否决。这正是本单的成因：判据只管一族，
  下一族复发时零告警。同族已在同一窗口复发过一次（check_device → ensure_root），
  不受理第三次。
- **把 16 族全部登记进一张预算表**（名字 → 预算）：否决。15 族没有预算声明可比，登记表会
  变成「给没有的事实编数字」；而解析源码的做法对新声明**自动生效**，不需要维护。
- **判据改成「timeout ≥ 预算」而不留余量**：否决。预算不是墙钟：步骤还要回 HOME、写 stdout、
  收尾，余量 30s 是 #2981 落地时就采用的口径（`180 >= 150 + 30`），本 PR 沿用同一数字，
  避免出现两套口径。
- **顺手把 guard 的 EXCEPTIONS 机制（版本 + 理由 + 删除条件）也建起来**：本 PR 不需要 ——
  当前两条不变式（pin / timeout）在仓库面上都是零例外，先让判据硬起来；#3109 讲的
  pin 守卫基线方向是另一件事，不夹带。

## Verification

- `pytest -q tests/test_template_step_timeout_vs_script_budget.py tests/test_pipeline_template_script_pins_2865.py`
  → **8 passed**
- **变异自证**（把 `ddr.json` 的 ensure_root 墙钟在临时副本里改回 60，不动工作树）：
  `ddr.json: script:ensure_root v1.0.2 timeout_seconds=60 < 预算 150 + 余量 30 ——
  引擎到点 killpg，脚本声明的重试/证据全都跑不到` ⇒ 判据确实能判红 #3087 的现场形状。
- 谓词红绿双向（`test_budget_parser_and_predicate_have_teeth`）：60/150 红、150/150 红
  （无余量）、None 红、字符串形态红、180/150 绿；解析器对真实脚本取值 = 150。
- 反空转（`test_budget_scanner_is_not_vacuous`）：覆盖族数 ≥2 且预算 >0。
- `scripts/run_gates.py check:quick` → OK；`check_governance_surface.py` → OK（S1–S15、S5x）。

## Revisit

- **只影响新建 Plan**：在飞 `plan_step` 已固化 `timeout_seconds`。生产 side 的存量已由
  plan 54 的一次性事务抬到 180s（#2802 r498/r503），本 PR 是**仓库侧回归面**。
- **余量 30s 是经验值**，不是推导值：它来自 #2981 落地口径。若将来某族的收尾成本超过 30s，
  应把它按族声明而不是全局限定；那时判据的 `_MARGIN` 要跟着分型。
- **`#3109` 的第二条**（前端计划编辑器 `EMPTY_LIFECYCLE` 是同一 pin/timeout 的另一个生产者，
  两个守卫都看不见）仍未覆盖：前端脚手架钉着 `check_device 1.0.0 @30s`，属 `#3109②` 射程。
