# 静默异常吞咽：只读审计工具与基线（#739 §2 的第一步）

Status: implemented
Class: process

## Decision

#739 §2 把「431 处静默异常吞咽」列为治理对象，但那个数字出自 2026-09-02 的一次粗测，
**口径与扫描面都没有固化**——无法复现、也无法判断治理有没有进展。本次只做第一步：

**固化口径 + 产出可复现基线**，交付 `tools/dev/audit_silent_exceptions.py`
（**只读、不阻断**，恒 exit 0；扫描面塌陷才 exit 2）。**不**引入门禁、**不**改动任何
被扫代码——是否上门禁、按什么阈值、怎么分批治理，等基线出来再由 owner 裁决（Revisit）。

口径（写在工具抬头，自检与用例双重守护）：

| 类别 | 含义 |
|---|---|
| `pass` | handler 体只有 `pass`（`...` 同义） |
| `continue` | handler 体只有 `continue`（循环里跳过） |
| `return_none` | handler 体只有 `return` / `return None`（把错误变成缺省值） |

**不计入**：handler 体里有任何其它语句——哪怕只是一行 `logger.debug`。本工具**不**
评判日志级别是否恰当（那是评审的事），只回答「这个异常消失得无影无踪了吗」。

扫描面与排除（`backend/` `tools/` `scripts/` 的生产代码）：

- 排除测试（`tests/` 目录、`test_*.py`）；
- 排除**已发布脚本版本**（`backend/agent/scripts/<name>/v<version>/`，ADR-0020 不可修改）；
- 排除 **alembic 历史 revision**（`backend/alembic/versions/`，#2258 不可改写）。

排除的理由是硬约束而非偏好：治理对象只能是**还能改的代码**。这三个面若计入，
规模会被冻结 artifact 淹没。

### 基线（2026-09-17，`origin/main` `1f22c951` + 本工具）

- 扫描 **390** 个生产文件；
- 全量（含冻结面）**922** 处；**可治理面 253 处**：
  `pass` 118 / `continue` 53 / `return_none` 82；
- 命中文件 112 个，前几名：`backend/core/metrics.py` 16、
  `backend/agent/pipeline_engine.py` 14、`tools/dev/ai_work.py` 12、
  `backend/realtime/console_registry.py` 9。

**与 issue 里 431 的关系**：不可直接比较（口径与扫描面都不同）。本工具的数字可由
`--json` 完整复现——这正是本次要解决的问题。

## Alternatives

- **直接照 431 这个数字上报/关门**：否决。数字不可复现，治理进度也就无法度量；
  先把口径钉死比先修几处更有价值（同 #2287「指标生产者」那次的做法：先固判据再清存量）。
- **把本工具接成阻断门禁（现在就上棘轮）**：本轮不做。253 处里相当一部分是**有意**
  的（探测/降级路径，如 `resolve_shared_storage_root` 的 Optional 探测、
  `zlib.error` 容错），一刀切红会逼出「加一行无意义 debug 日志」的应付式改动——
  与 #736 里被否掉的「私有函数数 ≤5」同一种病。先有基线，再谈阈值。
- **口径里纳入「有日志但只 debug 级」**：否决。那是**日志质量**问题，不是「静默」；
  混进来会让判据变主观，且信号量级完全不同（前者可能上千）。
- **扫描面含测试与冻结 artifact**：否决。见上，治理面之外的代码改不动，计入只会
  稀释信号（实测 922 vs 253）。

## Verification

- **反例构造（先证伪再采信）**：
  - 关掉冻结面排除（`_is_frozen` 恒 False）→ `test_frozen_surfaces_are_excluded` 与
    工具自检 **FAILED**；
  - 把「含有其它语句」也判为静默（`len(body) != 1` 分支返回 `pass`）→
    `test_handler_with_any_statement_is_not_silent` **FAILED**。恢复后 6 passed。
- 实测命令与结果：
  - `python tools/dev/audit_silent_exceptions.py --self-test` → 通过（三类判定 + 冻结面）；
  - `python tools/dev/audit_silent_exceptions.py` → 390 文件 / 253 处（数字见上表）；
  - `TESTING=1 python -m pytest tests/test_silent_exception_audit.py -q` → **6 passed**；
  - `python -m ruff check tools/dev/audit_silent_exceptions.py` → All checks passed。

## Revisit

- **是否上棘轮门禁**（owner 裁决）：基线已有，可选形态有
  ① 「不得净增」（差异面判据，参考 `check_invariant_diff.py` 的 base-ref 机制）、
  ② 绝对封顶（同 #736 的棘轮，但 253 的基数远大于 #736 的三个文件，需按文件分档）、
  ③ 只对**新增文件**生效。三种都影响 PR 路径的注意力预算，需要与 #736 的门禁家族一起看。
- **治理批次**：若决定治理，建议按**子系统**分批（agent 执行链 / watcher / site_config /
  tools），而不是按文件计数排序——同子系统的吞咽往往同因（如「探测失败即降级」），
  一起改才能写成同一条策略，也便于回滚。
- **`return_none` 是最大的一类（82）**：它比 `pass` 更隐蔽（调用方拿到缺省值却不知道
  发生了异常）。若要优先治理，建议从它开始，并顺带定「什么情况下允许返回缺省值」的
  判据（例如：只有 `Optional` 语义明确的探测函数允许）。
- **口径演进**：若将来把「只记 debug 日志」纳入统计，应作为**独立指标**（如
  `log_level=debug`）而不是扩大本工具的 `total`——否则基线不可比。
