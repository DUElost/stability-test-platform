# dev bootstrap 空库丢字典 seed：alembic 提为主路径，`create_all` 退为标注兜底（#2381）

Status: implemented
Class: bug-fix

## Decision

**`init_dev_db.py` 的默认路径改成 `alembic upgrade head`；`create_all` 只保留为「有表但无
`alembic_version`」的 legacy dev 库兜底，且必须在输出里自报代价与出口。**

缺陷本体（实测地面真值，一次性 postgres:16 容器）：旧脚本对空库执行
`Base.metadata.create_all()` ⇒ **38 张表 / `specialty` 0 行 / 无 `alembic_version`**。
`specialty`、`script` 这类静态字典的唯一事实源是 seed 迁移，后端**没有写端点**，所以
dev 环境里「新建 Plan → 专项无可选项」是必然而非偶发，且现场看起来完全像 schema 问题。
误导来源是脚本自己那句不区分路径的 `print("dev_db_schema_ready")`——两条通道在日志里同形。

修复的立脚点不是「让 create_all 多 seed 一点」，而是承认**链本来就能从空库自举**
（#2381 已在两个 scratch 库上实测 `alembic upgrade head` from empty ⇒ 6 行 specialty +
脚本注册表）。旧 docstring 声称链做不到，这个过期前提才是脚本绕道的根因。

落法：

1. **`backend/scripts/init_dev_db.py`** 拆出纯函数 `_choose_bootstrap_path(tables) -> str`
   做选路（空库 / 已在链上 → `alembic`；有表无版本 → `create_all_legacy`），
   `_bootstrap_schema()` 只负责 IO。alembic 分支**复用
   `backend.scripts.check_schema_sync._run_upgrade`**——它带着 #934 的 ambient
   `DATABASE_URL` 语义（`alembic.ini` 自带 sqlite 占位，`env.py` 在导入期用环境解析结果
   覆写 config URL）。在同一仓库里写第二份 alembic 调用，等于再造一次「连错库」。
   ⚠️ `_run_upgrade` 是私有名但被 `backend/tests/test_schema_sync_guard.py:29` 引用，
   **不要重命名**。
2. **兜底必须标注**（AGENTS.md：未标注的止血会沉淀成债）。`path=create_all_legacy
   WARNING=no_alembic_version dictionary_seeds_not_applied:` + 具体后果（specialty 不补齐
   ⇒ Plan 建不出来）+ 终态出口（核对 schema 后 `alembic stamp` 再 `upgrade head`，或重建空
   dev 库）。收养老库需要显式决策，不该由一个 dev 脚本顺手做掉，所以不自动 stamp。
3. **`main()` 只在 alembic 路径打 `dev_db_schema_ready path=alembic`**，裸行消失。
4. **`docker-compose.yml` 不改**：`backend/tests/test_deployment_files.py:46` 断言 compose
   仍含 `python /app/backend/scripts/init_dev_db.py`，保持单入口既修好真值又不裂成两条
   bootstrap 路径。
5. 文档：`docs/development/local-development.md` 新增「dev 库的 schema 与字典 seed」小节
   （三行现状/路径/输出对照表 + 收养出口命令 + 判据位置）。

### 修的过程中真实踩到的坑（已固化为判据）

把 `from backend.core.env_source import resolve_database_url` 放在**模块顶层**——而该文件
在 `_REPO_ROOT = ...; sys.path.insert(...)` 之前就已执行顶层 import，compose 又是脚本方式
调用（`python /app/backend/scripts/init_dev_db.py`，`sys.path[0]` 是脚本目录）。结果
ModuleNotFoundError，而 **`ruff check` 全绿**（它不模拟运行时 sys.path）。原文件把全部
backend import 收在函数体正是为此，这条约定此前没有任何机器判据守着。现在两条：一条 AST
判顶层不得 import `backend.*`，一条以 compose 的真实调用形态（脚本绝对路径 + 仓库外 cwd）
跑模块体，用 `ENV=production` 早退分支证明「模块体可执行且顺序正确」，且不碰数据库。

### 有意接受的副作用

对**已在链上**的 dev 库，脚本现在会跑 `upgrade head`（旧行为是 `create_all` 空转）。也就是
说 dev bootstrap 从「只建表」变成「会应用待应用迁移」。这与 `local-development.md` 的手工
路径一致，也是 compose 反复 `up` 的正常期望；红线仍是 `_refuse_production()`，且它必须先于
任何 schema 动作执行（已加判据）。

## Alternatives

- **给 `create_all` 后面补一段手工 seed 插入**：被否。字典 seed 的事实源就成了两处，
  下一次加 seed 迁移必然漏同步——正是 #2399 那类「三面同源靠人记」的形状。
- **让 legacy 分支也自动 `alembic stamp head`**：被否。stamp 的前提是「库内 schema 确实等于
  head」，脚本无法证明；猜错就是把一个漂移库伪装成已对齐，之后所有迁移都会静默跳过。
- **只改文档、不改代码**（写明「dev 起来后要自己跑一次 alembic」）：被否。这把必现缺陷转成
  使用纪律，而 #2381 的成因恰恰是文档写错了前提——纪律层不可信。
- **在 compose 里加第二个初始化步骤跑 alembic**：被否。两条 bootstrap 路径会分叉，且撞
  `test_deployment_files.py` 的单入口断言。
- **纯文本判据（匹配 `if not tables`）**：先写了，实测拦不住 `if False` 这种「形似保留、
  实质退回 create_all」的中性化变异，改成打在纯函数上的行为判据。

## Verification

只列实跑项。

- **容器地面真值（一次性 postgres:16，非本机 dev 库）**
  - before（HEAD 版脚本，空库）：`tables=38 specialty=0 alembic_version=absent`
  - after（空库）：`rc=0`、`tables=39`、`specialty=6`、`script=46`、
    `alembic_version=d4e5f6a7b8c9`（= 当时的 head）、`admin/admin`
  - 幂等（第二次跑）：`rc=0`、`dev_db_schema_ready path=alembic`、specialty 仍 6
  - legacy 库（先 `create_all` 建 38 表、无 `alembic_version`）：`rc=0`、走
    `path=create_all_legacy`、告警文案完整、`specialty=0`（兜底如实报缺，不冒充成功）
  - 全部命令均在容器隔离库上执行；本机 postgres 未接触。
- **`tests/test_dev_bootstrap_seed.py`**（7 例，PR 路径 `pr-agent-tests` 覆盖）：`7 passed`
- **`tests/test_alembic_upgrade.py::test_dev_bootstrap_from_empty_database_produces_seeded_schema`**
  （夜间容器）：`1 passed in 4.85s`
- **红绿自证**：7 个变异全部 on-target，无附带红——M1 backend import 提顶层 / M2 判定条件
  中性化 / M2b 判定吞掉空库 / M3 兜底不标注 / M4 退回裸 ready 行 / M5 生产守卫后置 /
  M6 另写一份 alembic 调用。变异后文件已从 `/tmp` 备份还原，`git status` 干净。
- `python scripts/run_gates.py check:quick`：`[OK] check:quick (10 gates)`
- `env -u DATABASE_URL python -m pytest tests/ --ignore=tests/test_alembic_upgrade.py
  --ignore=tests/test_script_seed_governance.py`：`1235 passed`（基线 1228 + 本单 7 例）
- 本机为生产控制面候选：以上全部跑在一次性容器 / 无 `DATABASE_URL` 的离线子进程上，
  本机 postgres 与 `.env.backend` 未被读写。

## Revisit

- **兜底的删除条件**：一旦确认再没有「有表无 `alembic_version`」的 dev 库（输出里
  `path=create_all_legacy` 不再出现），第 2 条兜底就是纯债，应删——连同那条 legacy 判据。
- **ADR-0008 证据行需要跟进**：`docs/notes/process/2026-09-11-doc-architecture-integrity.md:42`
  把 `backend/scripts/init_dev_db.py:1-3` 记为「唯一 `create_all` 出现处」。本次改动后
  事实仍成立（仍在该文件、仍 `DEV ONLY`、仍 `_refuse_production`），但**语义从「主路径」变
  成「标注兜底」**。若后续把这段登记进 ADR 证据表，措辞要跟着变，否则下一个人会以为
  dev 建库靠 create_all。
- **head 版本号不写死**：容器判据用 `ScriptDirectory.get_current_head()` 动态取。#2399 的
  教训就是守卫版本比 seed 版本小一整个 revision；把 `d4e5f6a7b8c9` 抄进断言会在
  #2424 合并后立刻变成假红。
- **`_run_upgrade` 的私有名耦合**：现在有两处跨模块引用（`check_schema_sync` 的守卫用例 +
  本脚本）。如果第三处出现，就该提成一个公开缝（例如 `run_upgrade_head(url)`），而不是
  继续依赖私有名。
