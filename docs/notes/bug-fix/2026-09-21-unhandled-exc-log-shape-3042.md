# 未捕获异常的日志形态：每种族全栈一次、之后一行（#3042）

Status: implemented
Class: bug-fix

- 日期：2026-09-21
- 关联：`#3042`（本单）、`#2959`（成因面：PG 连接槽耗尽，当时正在实时发生）、
  `#2960`（上一轮日志刷屏，同「体积靠人发现」病根）、`#3020`（access log 层，其前提被本单位移）、
  `#1958`（「只在日志里可见」的收敛先例）、`#1927`（日志/指标键必须有界）
- 落点：`backend/core/exception_log.py:1`（新增）、`backend/main.py:379`（接线）、
  `backend/tests/test_unhandled_exception_log_3042.py:1`

## Decision

**先量再改**。本机（=生产控制面）`logs/backend.log` 尾部 8 MB 只读分类：

| 测量 | 值 |
|---|---|
| 该 8 MB 行数 | 98,440 |
| traceback 记录数 | 228 |
| 终端异常类名 | `TooManyConnectionsError` 227 / `DeadlockDetectedError`(+`DBAPIError` 包裹) 3 |
| 单条记录行数 | 367（#3042 正文实测中位） |
| 发射点 | `backend.main` `Unhandled exception on <METHOD> <path>`（**225 条**），即 `global_exception_handler` 的一句 `logger.exception` |
| uvicorn 是否另外重复打栈 | **否**：同窗口 `Exception in ASGI application` 命中 **0** 次 ⇒ 一次失败只有一份栈，收掉它就收掉了全部体积 |

即：**这 8 MB 几乎全部由一行代码产生**，而它记录的事件（借不到连接）本身是秒级可描述的。
Python 3.13 的 fine-grained error locations 让 chained traceback 的每个帧再带 `^^^` 续行，
把「3 层链」放大成 367 行——这是解释器形态，不是我们的 bug，但体积归我们管。

**取舍**：既不是「压成一行」也不是「保留全栈」，而是
**每种失败族全栈一次，之后每族只记一行**（`#3042` 判据 1+2 的合取）。
理由：某一种失败**首次**出现时，栈是唯一能定位根因的东西；第 228 次出现时它只是把
现场可查时长压到 1/8 的元凶。稳态那一行必须留得住定位维度（判据 1）：

- 异常类链 `DBAPIError<...>TooManyConnectionsError`（谁抛的 / 根因是谁）；
- **SQLSTATE**（`53300` 槽耗尽、`40P01` 死锁、`08xxx` 连接中断——分类本身）；
- 端点**模板**：直接复用 `backend/core/request_metrics.py::endpoint_label`（#743 已定口径，
  不新造一份归一化逻辑，也不写带 ID 的原始 path）；
- 客户端 host（今天只有 uvicorn access 行带它；#3020 若关掉 access log 就彻底没了）。

**判定面是类型不是文本**：链上出现 `sqlalchemy.exc.DBAPIError`（PG 服务端错误经方言翻译的
落点，槽耗尽/死锁都在此）或 `sqlalchemy.exc.TimeoutError`（池排队超时，同一失败面的另一半）
才算本族；`StatementError` / `InvalidRequestError` 等 SQLAlchemy **编程性**错误继续全栈。
文本匹配会在下次改文案时静默失效，故不采用。族键有界（128 种族，FIFO 让位；让位后重现将
再得一次全栈——宁可多给一次栈，也不无界缓存）。

**状态码与响应体形状不变**（500 + `INTERNAL_ERROR`），旧文案 `Unhandled exception on ...`
对非 DB 异常逐字保留（同 #2960「排查习惯不破」）：本单只治体量，不改对外契约。

## Alternatives

- **全压成一行 / 直接删日志**（否决）：判据 1 明写「整段压成一行和删掉日志都是把婴儿倒掉」。
- **一律保留全栈，靠轮转兜住**（否决）：轮转已生效（#2205/#1265），代价是把现场时长压到 1/8，
  恰是本单的事故面。
- **给 `logging` 挂截断 filter（栈只留前 N 帧）**（否决）：这是全局生效的，会把**真 bug** 的诊断
  一起截断；失败族与非失败族在 filter 里无法区分，而在异常处理器里可以。
- **改状态码为 503 让 Agent 退避**（否决，越界）：`#2959` 管容量与告警面、ADR-0047 D1/D2 待裁；
  在这里改对外语义会把「治日志」变成「改重试契约」，且与 #3026 在办的告警口径耦合。
- **首条全量 + 后续计数用指标承载**（部分采纳）：计数走日志族键即可；新增 Prometheus 标签
  会碰 `backend/core/metrics.py`（注释标明另有在办会话）并引入基数评审，另议。

## Verification

- `pytest backend/tests/test_unhandled_exception_log_3042.py -q` → **8 passed**（新增）
- **判别力双向**：把 `if db_failure is None:` 改成 `if True:`（还原成「一律全栈」）→
  `test_db_failure_first_of_family_keeps_one_traceback` 与
  `test_db_failure_repeat_is_one_line_and_keeps_locating_dimensions` **FAILED**；还原后复绿。
  用例侧同时钉住：稳态 `exc_info is None` + 渲染后**恰好一行** + 四个定位维度齐备 +
  `/boom/41981` 不得泄漏进日志 + 非 DB 异常仍 `exc_info is not None`
- 分类表单测覆盖 4 形态：`DBAPIError(<TooManyConnectionsError>)` / 池 `TimeoutError` /
  `ValueError` / `InvalidRequestError`（后两者必须**不**降级）
- 族键有界性：灌 168 个种族后 `len(_seen_families) <= 128`，且被让位的族重现时重新算「首次」
- 集成测试挂在**真实 app** 上（`fastapi_app` + 临时路由，测试后移除），走真实 handler 注册链；
  注释记录了一个非显然事实：`Exception` handler 由 `ServerErrorMiddleware` 调用后**仍向上抛**，
  故 TestClient 需 `raise_server_exceptions=False`
- `pytest backend/tests/api/test_main_lifespan.py backend/tests/test_api_docs_switch.py -q` → **18 passed**
  （`backend/main.py` 改动未触启动/关闭与 docs 开关面）
- `ruff check` 三个文件 → All checks passed
- `scripts/run_gates.py check:quick` → **14 gates OK**；`scripts/run_gates.py check:pr` →
  **[OK] check:pr (23 gates)**
- 事故本身已回落（不改判据，只是记录现场）：本机 `backend.log` 最近 6 MB 覆盖
  21:25:22 → 21:51:48 / 43,906 行，`Unhandled exception on`
  命中 **0** 次 ⇒ 本轮槽耗尽峰值约在 21:16–21:25，之后平息；但**放大器仍在 main 上**，
  下次槽耗尽仍会重演，这正是本单与 #2959 分治的理由
- 待跑（pending）：PR required checks（`backend-test` 在 PR 阶段 skip，本用例随夜间全量跑）、
  合并后**上线窗实测**（见 Revisit 第一条）

## Revisit

- **上线后必须实测一次，不得以「结构上少了很多」交差**（#2960 的教训：修复合入 ≠ 验收）：
  下次同类事件里量 `logs/backend.log` 尾部窗口内 `Unhandled exception on` 与
  `unhandled_db_failure` 的行数/字节占比。若 `unhandled_db_failure` 仍占大头，说明
  **单条一行也不够**——那时才轮到判据 3（增速自观测）与聚合窗口出场。
- **判据 3（日志增速自观测）本单未做**：#2960 与 #3042 两轮都是「体积问题靠人发现」。
  终态应是 textfile/Prometheus 侧的 MB-per-hour 指标 + 告警，判据可写成
  「增速超阈值 ⇒ 第一行源必须可命名」。这是新增观测面（新指标 + 新告警 + 场景用例），
  属另单，需批准后立案。
- **`Exception` handler 仍 re-raise 这一事实**：本单只保证「栈的份数」受控；若将来
  #3020 之外还有人想让 5xx 语义化（503/退避），要先处理这条传播链，不是在 handler 里改返回值。
- **WebSocket/SocketIO 路径不在本单射程**：`app = ASGIApp(sio_server, fastapi_app)` 的 SIO 侧
  异常不经此 handler；本次事故全在 HTTP 侧，故不外推。
