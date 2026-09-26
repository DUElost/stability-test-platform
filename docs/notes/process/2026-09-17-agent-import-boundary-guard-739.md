# Agent 生产代码的 import 边界守卫（#739，静态 AST）

Status: implemented
Class: process

> **更名注记（2026-09-26）**：本笔记提到的 `test_pipeline_validator_parity_738.py` 已更名为
> `backend/tests/core/test_pipeline_validator_contract_738.py`（内容自 ADR-0054 起已从双端 parity 改为单实现 + 布局对拍；#3401 A1）。

## Decision

给 `backend/agent/` 的**生产代码**（含 `scripts/` 下已发布脚本，不含它自己的测试）加一条
静态守卫：只允许 import `backend.agent.*`，跨包 import 必须登记在
`tests/test_agent_import_boundary.py::_SHARED_ALLOWLIST` 里并写明理由（当前两条：
`backend.core.legacy_aee` 常量表、`backend.core.pipeline_validator` 校验器，均已人工
核实为纯模块）。判据为 AST：静态 `import` 与 `importlib.import_module("…")` /
`__import__("…")` 的字面量形态都算；扫描面塌陷与豁免表过期各自有专门用例。

边界规则同步写进 `backend/agent/AGENTS.md`（scoped 文档），避免只活在测试里。

### 为什么是生产侧，而不是最初提的「测试侧」

立项时的假设是「agent **测试**越界 import 控制面，补一条静态守卫、对 4 个文件做
allowlist」。实测推翻了这个前提（也因此没有按原方案做）：

1. **越界面远大于 4 个文件**：`backend/agent/tests/` 里 import 控制面模块的文件实测
   **14 个**（含 `backend.api` / `services` / `tasks` / `realtime` / `scheduler` /
   `models`）；issue 里的「4」只是**干净环境下会失败**的那 4 个。
2. **`backend/core` 是混合层**：`pipeline_validator` 的 parity 测试
   （`test_pipeline_validator_parity_738.py`）**就是要**同时 import 两端——按包名一刀切
   会把这类正当用例判红。真正的边界只能按模块定义，不能按顶层包。
3. **测试侧的「运行时判据」已经被有意兜住**：agent conftest 在收集期 `setdefault`
   `DATABASE_URL` / `JWT_SECRET_KEY`（#2428，为让套件在干净 shell 可跑）。
   反例实测：往 agent 测试里注入 `import backend.api.routes.agent_api` 后，
   `env -i … --collect-only` **仍 2130 通过**——`agent-tests-collect` 门禁的注释仍宣称
   「能拦住重新引入控制面 import」，**实际已经拦不住**（该门禁仍未修，见 Revisit）。
4. **部署面才是真风险**：Agent 跑在没有控制面的主机上，一条
   `import backend.services.x` 会让目标机直接 `ImportError`。测试里 import 控制面是
   **测试策略**问题（要不要为控制面行为写 agent 侧用例），不是部署风险——
   后者该由 owner 裁决（见 Revisit）。

## Alternatives

- **按最初的方案给测试侧做 14 文件 allowlist**：否决。判据会与 #2428 的取向相抵
  （conftest 供 env 是**有意**的），且 14 条 allowlist 会把「冻结」做成大规模豁免，
  拦住新用例的代价高于收益。测试侧怎么收敛留给 owner。
- **恢复 `agent-tests-collect` 的牙齿（去掉 conftest 的 setdefault）**：否决。那会
  把 #2428 修好的「干净 shell 恒红 23 例」重新引回来——用一个真问题换一个门禁。
- **把「不 import 控制面」从文档里删掉**：否决。生产侧的 ImportError 风险是真的，
  只是原来没有任何东西拦它。
- **用运行时探针（在无控制面依赖的解释器里 import agent 包）**：否决。那需要一套
  模拟 host 的依赖环境（agent 侧依赖集与控制面不同），成本远高于静态 AST；
  且静态判据对「新增一条越界 import」的拦截更早、更快。

## Verification

- **反例构造（先证伪再采信）**：
  - A 往 `backend/agent/heartbeat.py` 注入 `from backend.services import script_catalog`
    → `test_agent_production_imports_only_allowed_shared_modules` **FAILED**；
  - B 抽掉 `_SHARED_ALLOWLIST` 里的 `backend.core.pipeline_validator` → 同一用例
    **FAILED**（该 import 变成未登记的跨包引用）。恢复后 4 passed。
- **写守卫过程中被自己的用例抓到一次实现 bug**：初版扫描器把 `backend.core.*`
  整体跳过，导致豁免表永远「未使用」→ `test_shared_allowlist_entries_are_still_used`
  红；改为「只允许 agent 包 + 显式登记项」后转绿。这条用例（豁免表过期判定）因此是
  承重的，不是装饰。
- 实测命令与结果：
  - `TESTING=1 python -m pytest tests/test_agent_import_boundary.py -q` → **4 passed**；
  - `TESTING=1 python -m pytest tests/ -q` → **1434 passed, 0 failed**；
  - `python tools/dev/check_governance_surface.py --check` → S1–S14、S5x 全绿；
  - `python -m ruff check tests/test_agent_import_boundary.py` → All checks passed；
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (11 gates)**。

## Revisit

- **测试侧的边界**（owner 裁决）：14 个文件 import 控制面模块，是「给它们做 allowlist
  棘轮」、「逐个解耦」还是「承认测试可以 import 控制面、把文档与门禁口径改过来」——
  三种都要 owner 定；本 PR 只保证**生产侧**不再新增越界。
- **`agent-tests-collect` 门禁的注释与现实不符**：它仍宣称能拦住「agent 测试重新引入
  控制面 import」，实测拦不住（conftest 供 env 之后）。修法要么改注释（说明它现在只拦
  「非 env 类的 import 期失败」），要么按上面的测试侧裁决一并处置——不在本 PR 内动它，
  避免在裁决前先改门禁语义。
- **动态 import 的变量形态**：`importlib.import_module(modname)`（变量）看不见。
  agent 生产代码当前无此形态；若出现，需要人工 grep 或扩展到数据流分析。
- **豁免条目的传递依赖**：本守卫只判直接 import；两条现有豁免已人工核实为纯模块
  （`legacy_aee` 只有常量、`pipeline_validator` 只用 json/pathlib/typing）。新增条目
  必须照做——这一条写在用例 docstring 与 `AGENTS.md` 里。
