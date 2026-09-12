# conftest 容器清理兜底：sessionfinish / 信号 / atexit（#1492）

Status: implemented
Class: feature

## Decision

#1492（#1482 的 follow-up）：testcontainers 的 ryuk 在本机未可靠回收，被
kill/超时或正常退出的 pytest 都可能遗留容器（实测 36 个、最老 >2 周）。
本单在**进程内可控退出路径**上主动停掉**本进程**的容器，不依赖 ryuk：

- 新增 `backend/tests/container_lifecycle.py`：`ContainerCleanup`
  —— `stop()` 幂等（重复调用只停一次）、best-effort（异常不外溢，避免卡住
  退出）；`register()` 挂 `atexit` + `SIGTERM`/`SIGINT` 处理器；
- 信号语义保持：处理器先停容器，再恢复前一个处理器——可调用则链回
  （pytest 的 SIGINT 走 KeyboardInterrupt 语义），否则恢复默认并
  `raise_signal` 原样重发（SIGTERM 退出码不变）；
- `backend/tests/conftest.py`：容器创建后立即注册守卫（按文件路径加载，
  与 #1300 护栏同款避免包级导入副作用）；新增
  `pytest_sessionfinish` 钩子在正常结束时停容器；显式 `TEST_DATABASE_URL`
  （无容器）时全路径 no-op；
- **SIGKILL 无法拦截**——由 #1482 的巡检工具（`--strict` / `--prune`）兜底；
- `testing.md` 在容器小节补兜底行为与 SIGKILL 例外。

## Alternatives

- 只靠 ryuk：本机实测不可靠（36 个残留跨 2 周），不能作为唯一防线；
- 在 conftest 里用 `pytest_unconfigure`：与 sessionfinish 等价，但
  sessionfinish 更早且语义明确（`unconfigure` 在插件拆除阶段，异常易被吞）；
- 直接改 testcontainers/ryuk 配置：根因未定位（ryuk 容器本身也在残留列表
  里出现过），先做进程内兜底，ryuk 根因留 Revisit；
- 清理所有 testcontainer（不分进程）：会误停其他会话的活跃实例，不做。

## Verification

- `pytest tests/test_container_lifecycle.py`：6 passed——stop 幂等 / 异常
  吞掉 / 无容器 no-op / 注册一次（atexit + 两信号）/ SIGTERM 停容器后重发
  默认信号 / SIGINT 链回可调用前处理器；
- **E2E（delta 法**：比较运行前后本机 postgres:16 容器 **ID 集合**，避免被
  其他会话的容器干扰——早期用全局计数曾误判）：
  - 正常结束：before=1 → after=1（集合一致，本进程容器已回收）✅
  - SIGTERM：before=1 → 运行中=2 → after=1（集合一致）✅，pytest 进程按
    信号退出；
  - 干净版（去插桩后）复验：✅；
- `ruff check backend/ tools/ scripts/ tests/` 全绿。

## Revisit

- ryuk 未回收根因（ryuk 容器自身残留、客户端注册失败？）未定位——本兜底
  覆盖 SIGTERM/SIGINT/正常退出；若后续发现 SIGKILL 频繁（如 CI 强杀），
  需在 CI/工具链侧补 `--prune` 收尾；
- `pytest_sessionfinish` 停容器会让「同进程多 session」场景（`--forked`/
  xdist）行为变化：当前测试套件无 xdist worker 各自起容器的用法；若引入
  xdist，需要把守卫挪到每个 worker 会话（本单不预防性实现）。
