# 控制面机上显式测试库指向 loopback 即拒载（#2632 缺口③）

Status: implemented
Class: bug-fix

## Decision

`backend/core/db_url_guard.py` 增第 3 道闸：**本机是控制面时，显式 `TEST_DATABASE_URL`
指向 `localhost`/`127.0.0.1`/`::1`/unix socket（host 为空）一律拒载**，沿用
`STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1` 豁免（记 warning）。

**为什么这一闸不是锦上添花**：第 2 道闸（与运行时 `DATABASE_URL` 逐字比对）在本机
**恒空**——ambient 环境里没有 `DATABASE_URL`（它只存在于仓库根 `.env.backend`，而
`testing.md` §2 明令测试不得读取/复用该文件），conftest 传进守卫的
`runtime_database_url` 是 `None`。于是生产机上真正生效的只有「库名含 test」这一道
命名约定，而 2026-09-17 22:33 那次 `postgres@stp_test` 连库尝试——`.../stp_test@127.0.0.1`
——两道闸全过。它没造成损害**纯属 `stp_test` 在该实例上不存在**，不是防线拦住的。

**标记物**：「本机是控制面」= 仓库根存在 `.env.backend`（生产唯一 env 源）。只判存在、
不读内容——不把连接串带进测试进程。

**标记物必须回溯主检出**（本单最贵的一课）：`.env.backend` 是未跟踪的本地状态，
**链接工作树里没有它**。首版把仓库根写成 `Path(__file__).resolve().parents[2]`
（= 当前 worktree 根），红/绿验证时守卫**没有拦**，pytest 真的去连了
`127.0.0.1:5432/stp_test`（SQLAlchemy 认证失败，日志见 Verification）。而本机跑测试
的实际形态恰恰全是 worktree（并行 Execution 约定），等于这道闸漏掉了它唯一要保护的
场景。改为：worktree 的 `.git` 是文件，内容 `gitdir: <主检出>/.git/worktrees/<名>`，
顺它回溯三层取主检出根再判一次。

## Alternatives

- **只改文档/口径**（SOP 首步先查 `information_schema`、生产机 unset）：做了，但红线
  只靠自觉——事故本身就是「有人按文档示例写了 `127.0.0.1/stp_test`」；
- **按 host:port 与运行时 DSN 比对**：本机 ambient 无 `DATABASE_URL`，判据恒空——那
  正是缺口本身，用它补洞是循环论证；
- **读 `.env.backend` 的连接串做比对**：违反 `testing.md` §2「测试不得读取或复用
  `.env.backend`」，且把生产凭据拉进测试进程；
- **禁止一切 loopback 测试库**（不分是否控制面）：会让 CI（runner 上 `localhost:5432`
  就是它的测试 PG）与普通开发机全红——第 3 道闸只在控制面上生效，CI/开发机判据不变。

## Verification

- `python -m pytest backend/tests/core/test_test_db_guard.py tests/test_ci_test_db_guard_wiring.py -q`
  → **31 passed**（新增：4 种 loopback 形态拒载、远端放行、非控制面 loopback 放行、
  豁免放行、worktree 回溯两态、conftest 接线源码断言）；
- **红/绿实测**（在本机 worktree 内）：`TEST_DATABASE_URL='postgresql+psycopg://postgres:not-a-real-password@127.0.0.1:5432/stp_test' python -m pytest backend/tests/core/test_test_db_guard.py`
  → **exit=4**，采集期报 `TEST_DATABASE_URL points at a loopback address while this
  checkout carries the production env source (.env.backend)`；
  - 同一命令在**首版（只看 worktree 根）**下 exit=1 且**未拒载**：18 个用例全部只报
    `sqlalchemy` 连接/认证失败——即守卫静默失效、测试真的连了生产实例。这条差异就是
    「回溯主检出」那个改动的判据；
- CI 形状（`on_control_plane_host=False`，`_SAME_DB`）在契约测试里显式断言放行；
- `python scripts/run_gates.py check:quick` → 全绿（见 PR）。

## Revisit

- 标记物与控制面形态绑定：若 `.env.backend` 改名/移位（站点安装线正在推进，见 #2718
  的证据面改造），`CONTROL_PLANE_ENV_FILE` 与判定逻辑需同步；
- 若豁免（`STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1`）被频繁使用，说明场景合法而判据过
  宽，应改为显式白名单而不是长期豁免；
- 第 3 道闸只覆盖 `backend/tests/conftest.py` 这条路径。Agent 套件（`backend/agent/tests`）
  与仓库级 `tests/` 不 import 它——若那边也出现显式测试库，需同族接线（本单不扩大范围）。

关联：`2026-09-17-pg-application-name-2632.md`（同一单的缺口①，连接归属）；
[`production-diagnostics.md`](../../operations/production-diagnostics.md) §安全边界。
