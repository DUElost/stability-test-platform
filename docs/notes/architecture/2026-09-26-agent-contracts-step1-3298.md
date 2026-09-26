# ADR-0054 第 1 步落地：共享契约包 `backend/agent/contracts/`（pipeline_validator + legacy_aee）

Status: implemented
Class: architecture

## Decision

按 ADR-0054（Accepted v1.0）§5 第 1 步，落地共享契约包与第一批搬迁（#3298）：

- **新建 `backend/agent/contracts/`**（D1，随 `agent-code` 下发，不改 ADR-0040 输入集、
  三处排除集与 digest 合约）：
  - `contracts/pipeline_validator.py`：由 `backend/agent/pipeline_validator.py` 迁入；
  - `contracts/legacy_aee.py`：由 `backend/core/legacy_aee.py` 迁入（`LEGACY_AEE_SCRIPT_NAMES`
    + `LEGACY_AEE_TEMPLATE_NAMES`）；
  - `contracts/__init__.py` **为空**（D3：不做 re-export，避免包 init 把运行时拉进控制面）。
- **D6 工件定位**：新增 `resolve_pipeline_schema_path()`，以 agent 包目录（`parents[1]`）
  为锚点取父目录下的 `schemas/pipeline_schema.json`——仓库布局（`backend/schemas/`）与
  主机安装布局（`<INSTALL_DIR>/schemas/`）共用一条公式，替换原先的裸深度
  `parent.parent`（搬进 `contracts/` 后必然解析错）。`_load_schema()` 缓存行为不变。
- **导入规则（D3）**：
  - agent 侧全部改**同包相对导入**——`job_runner._validate_pipeline_def`（删除
    `try: backend.core… except ImportError` 双形态兜底）、`install_selfcheck`、
    `registry/script_registry`（`..contracts.legacy_aee`）；
  - 控制面侧改绝对导入 `backend.agent.contracts.*`：`api/routes/{pipeline,plans,schedules,scripts}.py`、
    `services/{ai_assistant/tools,plan_dispatcher_core,plan_dispatcher_sync,script_catalog,script_catalog_version}.py`。
- **D5 完成定义**（本 PR 全部满足）：删除 `backend/core/pipeline_validator.py`、
  `backend/agent/pipeline_validator.py`、`backend/core/legacy_aee.py`、
  `backend/agent/legacy_aee.py`（不留再导出壳）；兜底分支删除；
  `test_pipeline_validator_parity_738.py` 改为**单实现测试**（见下）；`.importlinter`
  C3 无对应基线行可删（5 条基线与本批无关，留给第 2–4 步）；
  `_SHARED_ALLOWLIST` 删 `legacy_aee` / `pipeline_validator` 两条（只剩 `metrics`）。
- **门禁（D4）**：
  - `.importlinter` C3 增加通配忽略 `backend.** -> backend.agent.contracts.**`，
    与首次搬迁同 PR 引入（零匹配在 `unmatched_ignore_imports_alerting=error` 下会报错）；
    C3 注释改为新口径（5 条基线 = 第 2–4 步的终态出口）；实测 C3 KEPT（21 ignored imports）。
  - `tests/test_agent_import_boundary.py` 增加 C6 AST 判据：
    `contracts/` 只许标准库 + 逐条登记的第三方（`_CONTRACTS_ALLOWED_THIRD_PARTY`，
    当前仅 `jsonschema`）+ 包内相对导入（解析后的模块必须在 `backend.agent.contracts` 内）；
    另按 D3 v1.0 覆盖 `backend/agent/__init__.py` 的轻量约束（只许标准库 +
    白名单叶子模块 `adb_wrapper`，白名单目标自身也被复用同一判据扫描，防间接绕过）。
- **测试改造**：`backend/tests/core/test_pipeline_validator_parity_738.py`（文件名保留
  历史）从「双端副本对拍」改为：① 单实现锚点（契约文件存在、两个旧副本不存在）；
  ② 仓库布局 schema 定位 == `backend/schemas/pipeline_schema.json`；
  ③ **主机布局真跑**——在临时目录复刻 `<install>/agent/` + `<install>/schemas/`，
  以 `PYTHONPATH=<install>` 子进程导入顶层包 `agent` 并跑历史语料，判定必须与仓库布局逐字一致
  （不变量从「两份代码不得漂移」变为「一种代码不得挑布局」）。
- **文档同步**：`backend/agent/DEPLOY.md` 目录布局加 `contracts/` 与 schema 解析说明；
  `backend/agent/AGENTS.md`（`CLAUDE.md` 为 symlink）import 边界段改写为新正门（40 行
  预算内压缩）；`tools/dev/check_script_packages.py` 的常量表来源注释改指；
  `docs/adr/ADR-0040` 的 `_schema_cache` 文件路径改为「搬迁后 / 搬迁前」双记录（不重写历史）。
- **棘轮**：`job_runner` 的双形态兜底归一后，`tools/dev/check_inner_imports.py`
  函数体内 import **597 → 596**（门禁提示要求同 PR 下调）。

## Alternatives

- **`backend/contracts/` 顶层包**：ADR-0054 备选 B 已否决——主机上会变成顶层 `contracts`
  包名错位，兜底与副本会原样复现，还要改 ADR-0040 输入集/排除集/digest 合约。
- **在 `backend/core/` 留再导出壳**：D5 明确禁止——壳会让 patch 目标分叉，测试假绿
  （与 #3292 不留重导出同理），调用方与 patch 目标已在本 PR 统一改指。
- **保留 `try: backend.core… except ImportError` 作过渡**：弃——契约已在 agent 包内，
  兜底分支是双份实现时代的产品；保留等于把「主机上必然失败的 import」留在热路径。
- **删掉 parity 文件、语料并入 `test_pipeline_validator.py`**：弃——parity 文件的价值
  在「两侧判定不分歧」，删掉会把这条不变量连载体一起丢；改为两种**布局**对拍，
  语料（含 None / 非 dict 根类型）继续服役。文件名保留历史以免动 3 份历史笔记的入链。
- **C6 用 import-linter 的 forbidden 合约**：ADR D4 已实测「祖先包写在
  `forbidden_modules` 静默不生效」，故仍用 AST 判据（与既有 #739 守卫同一套扫描）。

## Verification

- `backend/agent/tests/`（迁移影响面最大的套件）→ **2160 passed**（4m08s，systemd-run 6G 硬顶）；
- 控制面相关：`backend/tests/{api/test_pipeline_templates_stages,services/test_plan_barrier_timeout,services/test_script_catalog_version,services/test_plan_dispatcher}.py`
  → **89 passed**；`backend/tests/core/test_pipeline_validator.py` +
  `test_pipeline_validator_parity_738.py` + `backend/tests/test_legacy_tombstones.py` +
  `backend/agent/tests/test_install_selfcheck.py` → **17 passed**；
- 根 `tests/`（含 Ansible digest 合约、install artifacts、两个 import 棘轮）→ **56 passed**；
- `layering` gate（`--no-cache`）→ 5 条合约全 KEPT（C3 21 ignored imports）；
- **反向验证（临时变异，逐条已复原）**：
  - contracts 注入 `import requests` / 绝对导入 `backend.agent.aee` → C6 判据红；
  - `agent/__init__.py` 注入 `from . import agent_loop` → 轻量判据红；
  - `adb_wrapper.py` 注入第三方依赖 → 白名单目标判据红；
  - 定位函数退回裸深度 `parent.parent` → 单实现测试的仓库布局 + 主机布局两条同时红；
  - 复活 `backend/core/pipeline_validator.py` → 单实现锚点红。
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (17 gates)**（含
  `layering` 5 条合约全 KEPT、`inner-imports` 596/596、`gov-surface` 含
  `backend/agent/AGENTS.md` 40 行预算）；

## Revisit

- ADR-0054 §5 第 2 步已落地（`aee_metadata` / `aee_event_dirs` / `watcher_contracts`，
  见 `2026-09-26-agent-contracts-step2-3298.md`）；第 3–4 步未做：`artifact_digest`
  算法、`kernel_usb_faults`/`state_migration` 逐个判断——C3 剩余 2 条基线与
  `_SHARED_ALLOWLIST` 的 `metrics` 是终态出口，不是永久豁免；
- `_CONTRACTS_ALLOWED_THIRD_PARTY` 新增依赖必须逐条评审（ADR-0054 §7）；
- `contracts/` 出现运行逻辑即说明 D2 判据被突破，回审 ADR-0054；
- 本 PR 遗留一个命名债：`test_pipeline_validator_parity_738.py` 已不含 parity 语义，
  文件名保留历史；若第 2–4 步也走完后仍有读者被名字误导，再单开改名 PR 并修入链。
