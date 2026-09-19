# test 库 loopback 护栏的旁路收口与读取面登记（#2794）

Status: implemented
Class: bug-fix

## Decision

两处缺口（#2632 缺口③交付的补完）：

1. **判定加固**：`_is_loopback` 不再只看 `urlsplit().hostname`，改为
   `_host_candidates()` **枚举 DSN 在 libpq 语义下可能连接的全部 host**——
   - query 覆盖：`?host=` / `?hostaddr=`（libpq 以 query 为准，authority 段只是幌子）；
   - multihost：`h1:5432,127.0.0.1:5432`（逗号分隔逐个尝试，**任一** loopback 即拒）；
   - unix socket 路径：`?host=/var/run/postgresql`（本机实例的另一种写法）；
   - 循环段收敛：127.0.0.0/8（不止 `.1`）、IPv4-mapped IPv6、`0.0.0.0`/`::`。
   形态不可解析时 **fail closed**（按本机处理）——该判定只在「本机是控制面」时才被调用，
   宁可多拒一个非常规 DSN（有 `STP_ALLOW_UNSAFE_TEST_DATABASE_URL` 出口），
   也不放一个能绕过判定、把 `TRUNCATE` 打到生产实例的写法进来。
   `parse_qsl` 提到模块级（不在函数体内 import，不留棘轮增量）。
2. **读取面登记（接线面的「新增即红」）**：`TEST_DATABASE_URL` 的非测试模块读取方
   钉进 `tests/test_ci_test_db_guard_wiring.py::TestReaderSurface._READERS_OUTSIDE_TESTS`
   （当前仅 `backend/scripts/check_schema_sync.py` = 只读 schema 比对），新增即红、
   并在失败信息里写明「会写库就必须接 `guard_test_database_url`」。测试模块
   （`backend/tests/` 下）读取它只用于连通性判断、不直接建会话，故豁免。

## Alternatives

- **改用 SQLAlchemy `make_url()` 解析**：弃——`make_url` 对 libpq 的 multihost 与
  query 覆盖语义无表达（`?host=` 不会体现在 `url.host`），会把「能不能解析」误当
  「是不是本机」；本模块刻意保持**零新增依赖**（conftest 导入期可用）。
- **只在 conftest 入口继续加参数、不碰判定**：弃——那正是本次审计指出的形态：
  参数传对了，判定本身可绕过，闸门等于装饰。
- **四处测试文件也纳入登记表**：弃——它们只读变量做连通性判断（不建会话），
  纳入只会制造维护噪声；判据按「非测试模块」划界并把理由写进类文档串。
- **fail closed 改为 fail open（解析不出就放行）**：弃——控制面闸的失效方向必须偏向
  拒载：放行的代价是生产库被 `TRUNCATE`，误拒的代价是一行配置覆盖。

## Verification

- `backend/tests/core/test_test_db_guard.py`：新增 6 种旁路形态（query host /
  hostaddr / multihost / socket 路径 / 127.0.0.2 / v4-mapped）**全部拒载** +
  「off control-plane 仍放行」+「多 host 均非 loopback 仍放行」；
  反向验证：`_is_loopback` 退回单 hostname 判定 ⇒ **6 例 DID NOT RAISE**（命中新用例）。
- `tests/test_ci_test_db_guard_wiring.py`：新增读取面登记用例（当前清单 = 1 条），
  新增读取方未登记即红。两文件合计 **40 passed**。
- `python scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- 若将来 `TEST_DATABASE_URL` 出现第二个**会写库**的入口（新脚本/夹具），在登记表里
  补「接守卫」而不是补理由；登记表条目数增长本身就是接线面变宽的信号。
- libpq 若新增 host 覆盖形态（例如 URI 参数扩展），`_host_candidates` 需同步；
  判据是「枚举全部可能连接的 host」，兜底方向是 fail closed。
