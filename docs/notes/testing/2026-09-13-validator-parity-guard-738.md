# Agent Note: pipeline_validator 双端副本的语义一致守护（#738 一个切片）

Status: implemented
Class: testing
Issue: #738

## Decision

为 `pipeline_validator` 的**双端副本**加一道**语义一致守护**：
`backend/agent/tests/test_pipeline_validator_parity_738.py` 对一份语料断言
「agent 侧实现与 core 侧实现给出**完全一致**的结果（含异常行为）」。

**为什么不直接"消除重复"（issue 标题的写法）**：本地核实后，这份重复是**有意为之且承载功能**的——

| 事实 | 位置 |
|---|---|
| 两份副本逐字相同（均 138 行，`diff` 为空） | `backend/agent/pipeline_validator.py` vs `backend/core/pipeline_validator.py` |
| core 优先、**ImportError 回落** agent 侧副本 | `backend/agent/job_runner.py:156-159`（docstring 写明 "imported from core"） |
| **安装期**使用 agent 侧副本（那时控制面包不一定在路径上） | `backend/agent/install_selfcheck.py:39` |

即「独立部署的 agent + 安装期自检」需要这份回落副本。**消除它需要一次共享模块的设计决策**
（把校验器下沉为可随 agent 一起分发的东西，或改为 codegen/同步检查），不在本切片内。

而当前**没有任何东西保证两份副本同步**：一旦漂移，同一个 `pipeline_def` 会在 agent 侧与控制面侧
得到不同判定（agent 放行控制面拒绝的定义，或反之）。本切片把该不变量变成可回归断言。

## Alternatives

- **直接删掉 agent 侧副本、统一 import core**：不选。会破坏「独立部署 agent + 安装期自检」两条路径
  （`job_runner` 的 ImportError 回落与 `install_selfcheck` 正是为此存在）。
- **判据用「两文件逐字相同」**：不选。那会把一次**合法的等价重构**（改名、抽函数）也判红；
  真正要守的不变量是「两侧判定不得分歧」。故取语义一致 + 语料覆盖，并把**异常行为差异**也纳入比对
  （一侧抛错、一侧返回错误列表同样是漂移）。
- **把守护放在 `backend/tests/core/`**：不选。`backend/tests/` 不在 PR required path 的
  agent 作业里（`pr-agent-tests` 跑的是 `backend/agent/tests/`），放 agent 侧才能**在 PR 路径生效**。
- **顺带把"634 处局部 import"也纳入**：不选。本地实测缩进 import 为 **1942 处**（含测试），
  与标题的 634 口径不符，而 issue 正文（口径定义）当前取不到——不猜口径、不扩大范围。

## Verification

- `pytest backend/agent/tests/test_pipeline_validator_parity_738.py -q` → **2 passed** ✓
- **守护有效性（teeth）——用无副作用方式证明**：在 python 会话里先把 agent 侧实现替换为
  「总是接受」的假实现（**内存内注入，不改任何文件**），再导入该测试模块调用它 →
  **确实抛出 AssertionError**，报出 **10 条分歧**（例：
  `valid_disabled_step: agent=(True, []) core=(False, ["(root): Additional properties are not allowed ('name'…`）。
  即：该守护能捕获漂移，不是装饰。
- 语料覆盖：接受形态（lifecycle/script 步骤、disabled 步骤）+ 各类拒绝形态（顶层 `stages`、
  legacy `phases`、lifecycle 阶段内嵌套 stages、缺 `version`、非法 action 前缀）+ 边界畸形
  （`{}` / `None` / 非 dict）✓
- `check:quick` → 见 PR。
- **未验证（诚实标注）**：两份副本的**历史**是否曾发生过漂移（无历史证据，只证明"当前一致且今后有守护"）。

## Revisit

- **issue 的"消除重复"仍未做**：需先裁定共享模块的形态（随 agent 分发的独立包 / codegen / 同步检查）。
  裁定后本守护可退化为"同步动作的一部分"，或保留为最后一道防线。
- **issue 标题的「634 处局部 import」口径未复现**（本地实测 1942 处，含测试）：需要 issue 正文的
  扫描口径才能核验；口径明确前不动手。
