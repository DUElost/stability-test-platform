# Agent 套件恒红 23 例的真因是 ambient `JWT_SECRET_KEY`：conftest 补边界 + 门禁去掉遮蔽层（#2428）

Status: implemented
Class: bug-fix

## Decision

**根因不是 issue 写的「缺 saq/redis 本地依赖」——实测 23 例全部是同一个原因：
`backend/core/security.py` 在导入期硬校验 `JWT_SECRET_KEY`。**

证据（本机 main `85225bf0`，`env -i` 干净环境）：

| 命令 | 结果 |
|---|---|
| `env -i pytest backend/agent/tests/`（4 个涉事文件） | `23 failed, 56 passed` |
| 同命令 + `JWT_SECRET_KEY=...` | `79 passed` |
| 23 条失败的 traceback | **全部**是 `RuntimeError: JWT_SECRET_KEY environment variable must be set` |

失败按文件分布与 issue 记录逐字相同（saq_scan_pipeline 16 / p3_3_multi_instance 4 /
legacy_tool_cleanup 2 / cron_scheduler 1），但这些用例断言的内容与签名密钥无关，本机
`saq`/`redis` 也确实可用。issue 的归因把「CI 绿、本地红」当成了依赖缺失，而真实机制是
**遮蔽层**：CI 的 `pr-agent-tests` job env 与 `run_gates.py` 的 `AGENT_TEST_ENV` 都注入了
`JWT_SECRET_KEY`，只有文档推荐的入口 `python -m pytest backend/agent/tests` 没人替它注入。

同文件的既有事实也支持这个判断：`backend/agent/tests/conftest.py:14` 早就为
`DATABASE_URL` 做过同一件事，注释写明理由就是「CI / run_gates 各自注入了环境，保护不到
这个直接入口」（#1295）。`backend/tests/conftest.py:31` 也已直接设 `JWT_SECRET_KEY`。
**这条边界本来就归套件自己所有，只是漏了一个键。**

落法三件（缺一都只治一半）：

1. **补边界**：`backend/agent/tests/conftest.py` 在导入任何 `backend.agent.*` 之前
   `os.environ.setdefault("JWT_SECRET_KEY", ...)`。用 `setdefault` 不用赋值——调用方
   显式注入的值必须仍然优先（CI job env、排障时的临时覆盖）。
2. **去掉遮蔽层**：`ci.yml` 的「Run agent tests」step 与 `run_gates.py` 的 `agent-tests`
   gate 改成与上面 `agent-tests-collect` 同口径的 `env -i PATH PYTHONPATH=. ...` **真跑**。
   只修 conftest 而 CI 仍代供，等于把判据留给人不留机器——缺口下次会长回来且没人看得见。
   顺带删掉已无使用处的 `AGENT_TEST_ENV` 常量（这就是那层遮蔽的载体）。
   零额外成本：跑的还是同一批用例（本机 161s）。
3. **四判据守卫** `tests/test_agent_env_selfsufficiency.py`（PR 路径）：前两条是**行为**判据
   （子进程里 exec conftest，一次不带 ambient、一次带 `caller-wins`），后两条盯 ci/gate
   口径一致。之所以不用文本匹配：`setdefault` 挪到 backend 导入之后、或改成无条件赋值，
   文本上都「看起来没问题」，只有运行时判据能分辨。

## Alternatives

- **issue 建议 (a)：在 `test-env-self-check` skill 加一条「SAQ 家族本地红为已知，全量报告
  须以 stash 基线差分为准」**：被否。那是让每个改动者每次都为一条本可消除的红付出一轮
  stash 排查（#2414 现场付过一次）；且它以错误的归因为前提（缺依赖），写进 skill 会把
  错诊断固化成规程。
- **issue 建议 (b)：给 4 个文件补跳过标记使本地全绿**：部分采纳、部分否决。采纳其
  「conftest 注入」形态，否决其「跳过」形态——`skipif` 会把 23 条真实用例变成本地静默
  不跑，用一个看不见的覆盖洞换一个看得见的红。
- **文档要求开发者 `export JWT_SECRET_KEY=...`**：被否。把缺陷转成使用纪律，且新同学仍会
  先撞红。
- **取消 `pr-agent-tests` 的 job 级 `JWT_SECRET_KEY`**：被否。该 env 同 job 的其它 step
  （根 `tests/` 子集等）仍在用，为一个键动全 job 环境是更大的面。改 agent 那一步即可。
- **只改 CI/gate 不改 conftest**：被否。那会把 23 例红从「本地」搬到「CI」，只是换个地方红。

## Verification

只列实跑项。

- **真因判定**：`env -i` 下 23 例失败 traceback 全部为 `RuntimeError: JWT_SECRET_KEY ...`；
  加一个变量即 `79 passed`。
- **修复后本地全量**（与 CI 新 step 逐字同口径）：
  `env -i PATH=... PYTHONPATH=. python -m pytest backend/agent/tests/ -q` →
  **2126 passed in 161.74s**（0 failed；`JWT_SECRET_KEY` 与 `TESTING` 均未由外部注入）
- **红绿双向：5 个变异全部 on-target，且各自只打中对应判据**
  - N1 删 setdefault → `test_conftest_supplies_the_secret_when_ambient_is_clean` 红
  - N2 改无条件赋值 → `test_conftest_does_not_clobber_caller_supplied_secret` 红
    （另实测：变异后调用方注入的 `caller-wins` 确实被覆盖成占位值）
  - N3 setdefault 挪到 `backend.agent` 导入之后 → 同 N1 红（顺序判据生效）
  - N4 CI step 退回继承 job env → `test_ci_runs_agent_tests_with_ambient_env_stripped` 红
  - N5 本地 gate 恢复注入 env → `test_local_gate_matches_ci_agent_step` 红
  变异文件均从 `/tmp` 备份还原。
- 另实测：**修复前**同一批 4 文件在 `env -i`（无 JWT）下 `23 failed, 56 passed`，
  修复后 `79 passed`——即 issue 记录的红名单与数量被逐字复现并消除。
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (10 gates)`
  （第一次运行被 `gov-surface` S10 打回：Note 的 `Class` 写了 `bug-fix / devx`，必须与目录
  一致——门禁按预期工作，改回 `Class: bug-fix` 后全绿）
- `pytest tests/test_agent_env_selfsufficiency.py` → **4 passed**（新增，PR 路径）
- `env -u DATABASE_URL -u JWT_SECRET_KEY pytest tests/ --ignore=tests/test_alembic_upgrade.py
  --ignore=tests/test_script_seed_governance.py`（CI 同款 PR 子集，且**刻意剥掉** ambient
  凭据以顺带验证根 `tests/` 也不依赖它）→ **1238 passed**
- 本机为生产控制面候选：全程 `env -i` 无 ambient 连接串；未读写生产库、未读 `.env.backend`
  （conftest 的 `DATABASE_URL` setdefault 本就是 #1295 为此设立的隔离边界）。

## Revisit

- **夜间 `backend-test` 的第 89 行仍带 job env 跑同一批 agent 用例**（`-v --cov-append`）。
  刻意不改：它是覆盖率采样，PR 路径已经是不被遮蔽的强制点，动它换来的是重复证据而非新
  防线，却要冒本机无法完整复现的 coverage 环境风险。若哪天夜间 agent 用例数与 PR 路径
  明显不符，回来看这一条。
- **守卫目前只覆盖 `JWT_SECRET_KEY` 一个键**，但判据 1 是「干净环境下 exec conftest 后
  ambient 依赖必须齐」的运行时判据——套件将来若再需要别的 ambient（`REDIS_URL` 等），
  `env -i` 真跑会直接红，不需要回来加判据。
- **issue 归因需要更正**：#2428 标题写的「缺 saq/redis 本地依赖」在本机不成立。若某个
  harness 的 venv **真的**没装 `saq`，表现会是 `ModuleNotFoundError`（收集期就红），
  与本案是不同问题面——真遇到再另案，不要用本单的结论去解释那种红。
