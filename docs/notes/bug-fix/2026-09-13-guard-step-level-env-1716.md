# #1716 补步骤级 env 守卫——堵住 #1664 的静默绕过口

Status: implemented
Class: bug-fix

## Decision

在 `tests/test_ci_test_db_guard_wiring.py` 增补第二个解析器
`_backend_test_step_env_keys(step_name)`，按 job+step 名定位 `Run backend tests`
步骤的**步骤级** `env:`，断言其中不得出现 `DATABASE_URL`；并加一例防解析静默失效。
测试数 7 → 9。

**缺陷确认（先复现，不凭报告）**：#1664 的守卫只解析 job 级（4 空格 `env:`），
其报错文案还**推荐**「确需 DATABASE_URL 的步骤用步骤级注入」。但真正的失败条件
（`conftest.py:90` 导入期 `os.getenv("DATABASE_URL")`）与变量写在 job 级还是步骤级
**无关**。实测注入：

```yaml
- name: Run backend tests
  env:
    DATABASE_URL: ${{ env.TEST_DATABASE_URL }}   # YAML 合法
  run: python -m pytest backend/tests/ -v --cov=backend
```

→ 旧守卫 **7 passed（静默放行）**；而 `pytest backend/tests/` **exit 4 +
UnsafeTestDatabaseUrl**——#1547 原样复发。即守卫在它自己推荐的写法上留了口子。

**与既有 job 级断言的分工**：两者都保留，因为二者覆盖**不同步骤**：

- job 级断言：防「job 级设 DATABASE_URL」（会让 `Run backend tests` 与
  `Run agent tests` 同时中毒）；
- 步骤级断言：防「仅在 `Run backend tests` 步骤注入」——job 级解析器看不到。

**为什么只锁 `Run backend tests` 而不锁全部步骤**：另两处步骤级 `DATABASE_URL`
是**契约要求的正确形态**（`Migrate empty PostgreSQL database` 需要它跑 alembic；
`Run agent tests` 的模块在 import 期解析它但不连库，且 agent 测试有自己的
`backend/agent/tests/conftest.py`，不触发本守卫）。解析器实测能正确读出这两处的
`['DATABASE_URL']`，**只是不断言它们**——盲目扩大断言面会把 #1566 的正确修复判红。

## Alternatives

- **改为扫描「任何 import backend/tests/conftest.py 的步骤」** → 否决：需要静态推断
  每个步骤的 run 命令会 import 谁（`pytest backend/tests/` 的模式匹配尚可，但
  「间接 import」无法静态判定），脆弱且易漏；直接锚定已知的失败步骤更可靠。
- **断言 job 内所有步骤都不得含 DATABASE_URL** → 否决：会误伤 alembic 与 agent
  tests 两处**合法**注入，把 #1566 的正确接线判红——守卫不得与既有正确形态冲突。
- **改用一个「真实 import conftest」的集成测试代替静态断言** → 否决：那需要
  `TEST_DATABASE_URL` 且会启动 testcontainers，违反 #1569 确立的「纯离线 + 秒级」
  判据（本文件 9 例 0.04s），且把 docker 依赖引入 PR 路径。
- **在 ci.yml 里加注释警告步骤级注入** → 否决：注释不拦回归；本缺陷的成因正是
  「守卫的报错文案推荐了危险写法」，说明文案层面已不足以防护。
- **顺手把报错文案里的「步骤级注入」建议删掉** → 否决（本单范围）：该建议对
  alembic / agent tests 两处仍是正确的；问题不在建议本身，而在**守卫未覆盖被建议
  的那个面**。已在本单补上覆盖，文案保留。

## Verification

- `python -m pytest tests/test_ci_test_db_guard_wiring.py -q` → **9 passed**（0.04s，纯离线）；
- **缺陷复现（修复前）**：注入步骤级 `DATABASE_URL` → 旧守卫 **7 passed**（静默放行）；
  同环境 `pytest backend/tests/ --collect-only` → **exit 4** + `UnsafeTestDatabaseUrl`；
- **红绿双向（修复后）**：同一注入 → 新守卫 **2 failed**，报错直指
  `assert 'DATABASE_URL' not in {'DATABASE_URL'}`（归因到 `Run backend tests` 步骤）；
  还原 → **9 passed**；
- **无误报**：解析器对三处步骤实测输出
  `Run backend tests → []`、`Run agent tests → ['DATABASE_URL']`、
  `Migrate empty PostgreSQL database → ['DATABASE_URL']`——合法注入被正确读取但不被断言；
- 全量离线子集：`python -m pytest tests/ -q --ignore=tests/test_alembic_upgrade.py`
  → **235 passed**；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿；
- `ruff check tests/test_ci_test_db_guard_wiring.py` → All checks passed。

## Revisit

- **解析器结构耦合进一步加深**：现已依赖 job 名、step 名与两级缩进。两处解析器都
  有「找不到即 raise/红」的兜底，但若 `ci.yml` 引入 YAML 锚点、复用或重排缩进，
  应改为按 YAML 解析（`pyyaml` 已在 dev lock 中）。
- **同类残留面**：本单锁的是 `Run backend tests`。若将来新增另一个会 import
  `backend/tests/conftest.py` 的步骤（例如把 `backend/tests` 拆成多个 pytest 调用），
  须同步加入断言名单——否则又是同一形态的静默口子。
- **文案与守卫的一致性**：本缺陷的教训是「报错文案推荐了守卫未覆盖的写法」。
  日后修改本文件任何报错文案时，应先自问该建议是否已被某条断言覆盖。
