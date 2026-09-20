# 跨包导入符号静态守卫（#2946：PR 阶段可发现的导入面回归）

Status: implemented
Class: testing

## Decision

**缺口**：PR 阶段不跑 `backend/tests/` 全量（`ci.yml:315` 自陈），跨包改名类回归要等夜间
`main-ci-backstop`（UTC 18:00）。本窗口实例：`261959f7`（#736 抽取 `job_runtime`）把
`OutboxDrainThread` 迁到 `backend/agent/outbox_drainer.py`，`backend/tests/test_phase0_closure.py`
6 处旧导入漏改 ⇒ main 上 7 例 ImportError（#2941 修，PR #2942）。

**为什么不是 `--collect-only`**（实测否证，含缺陷的 main `9487a29b` 树上）：

```
$ python -m pytest backend/tests/ --collect-only -q
3418 tests collected in 4.93s        ← 全绿；导入在**函数体内**，收集期不解析符号
```

**做法**：新增 `tests/test_cross_package_import_symbols.py`——纯 AST、秒级、无需 DB，
落在 `tests/` 的**离线子集**里 ⇒ CI 的 `Run agent tests` 步骤（PR 路径）并行跑
`pytest tests/ -q` 时自动生效，**无需改 ci.yml**。

判据：`from backend.<mod> import <Name>` 必须能在目标模块静态解析到——顶层
def / class / 赋值（含带注解）/ 该模块自身的导入，或（目标是包时）同名子模块。
`ast.walk` 覆盖**函数体内导入**（#2941 正是这种形态）。

**不可判定的两类显式登记，不静默跳过**（本仓口径：不可判定 ≠ 通过）：

- `_PEP562_EXPORT_MODULES`：模块级 `__getattr__` 惰性导出（当前 1 处——
  `backend/core/security.py` 的 cookie 名，ADR-0042 P2）。由
  `test_pep562_export_modules_are_registered` 与仓库真值**对拍**：新增一处即红；
- `_NON_ENUMERABLE_TARGETS`：命名空间包（目录无 `__init__.py`，符号静态不可枚举；
  当前 1 处——`backend.alembic.versions`）。主用例断言未解析目标 ⊆ 本表。

## Alternatives

- **把 `backend/tests/` 搬进 PR 路径**：13 分钟量级（本机实测全量 796s），超 PR 耗时预算；
  本单只补「导入面」这一档（4s）。
- **`--collect-only` smoke**：实测抓不到（见上），且会给出「已检查」的错觉——本单明确否证。
- **backend 侧引入 mypy**：同类问题能覆盖，但存量错误面与耗时都大得多，属方向级决策。
- **只把 `test_phase0_closure.py` 加进 PR 路径白名单**：能治这一处、不治这一类
  （下次换个文件照样漏），且白名单会持续生长。

## Verification

- **反例（真仓实弹）**：把 #2941 的坏导入放回 `backend/tests/test_phase0_closure.py`
  （6 处 `from backend.agent.main import OutboxDrainThread`）→ 守卫**红**并逐处报出行号
  （86 / 188 / 554 / 562 / 569 / 577，与 #2941 的实跑一致）；恢复后绿。
- 真仓扫描：5197 个导入符号，**0 违规**（首版原型曾报 253/7 条，全是「包内子模块」与
  PEP 562 两类假阳——修正判据后归零，过程见本文件 Alternatives 与自检用例）。
- 守卫自检 5 例（含函数体内缺失符号、合法三形态、星号导入识别为不可判定）全绿：
  `pytest tests/test_cross_package_import_symbols.py -q` → **5 passed**。
- `ruff check` → All checks passed；`python scripts/run_gates.py check:quick` →
  **[OK] check:quick (12 gates)**。
- 接线无需改动：PR 路径已跑 `pytest tests/ -q`（`ci.yml:401`，忽略 3 个 PG 重文件）。

## Revisit

- **覆盖面**：当前判定面是「`from backend.* import <名>`」；不覆盖
  `import backend.x.y as z` 后按属性访问（`z.SomeName`）与 `globals()` 动态导出。
  若将来出现这类形态，按同一登记表模式扩展，不要改回静默跳过。
- **登记表的维护**：两张表（PEP 562 / 命名空间包）同时是「豁免」与「留痕」——
  新增惰性导出或星号导入时先确认理由再登记；`import *` 目前 0 处，一旦出现会被识别为
  不可判定并要求登记。
- 与 #2879（审计 resource_type 守卫 fail-closed）、`tests/test_agent_import_boundary.py`
  同属「静态守卫」族：判据都是「可判定就判、不可判定要留痕」。
