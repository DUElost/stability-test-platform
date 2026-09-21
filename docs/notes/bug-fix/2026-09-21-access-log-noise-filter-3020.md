# access log 降噪：丢高频内部轮询的 2xx 行（#3020）

Status: implemented
Class: bug-fix

## Decision

`backend.log` 在 #2960 修掉逐设备 INFO 刷屏后，**剩余最大单一行源变成 uvicorn access 行**（现文件 20h 内 1,308,968 行 = 25.4%，其中 99.99% 是 200、87.2% 来自 7 条 agent 内部轮询路径）。本 PR 按 #3020 的**方向 2**（保留但过滤）落地最小方案：

- **只丢「高频内部轮询路径 + 2xx」的行**（`backend/core/logging_setup.py`）：
  `ACCESS_LOG_NOISE_PATTERNS` 列 7 条路径（patrol-heartbeat / extend_lock / jobs claim /
  coordinator-heartbeat / recovery-sync / leases extend-batch / heartbeat），路径**末尾锚定**
  （`/api/v1/heartbeat` 不吃 `/api/v1/heartbeat-detail`），判定前剥 query；
- **非 2xx 恒保留**（`status_code >= 400` 直接放行）——404/422/500 那些行正是排查面所在，
  一条不丢；
- **fail-open**：`record.args` 形状不符（uvicorn 改版、记录被别的库改写）时**放行**，
  判据失配的方向是「不降噪」而不是「静默丢证据」；
- 逃生阀 `STP_ACCESS_LOG_FULL=1`（默认关）→ 逐条全记，用于现场确认「这次请求到底到没到」；
- 挂在 **logger**（`uvicorn.access`）而不是 handler：uvicorn 的 `dictConfig` 会重建 handler，
  但该 logger 的配置项里没有 `filters`，已挂的 filter 不会被清掉（实测钉子在
  `backend/tests/core/test_logging_setup.py::test_install_filter_survives_uvicorn_dictconfig`，
  哪天 uvicorn 加了 `filters: []` 这条就会红）。

调用点在 `backend/main.py` 模块级（`install_access_log_filter()`，紧邻既有的 access formatter patch）。
聚合视图由既有 `stability_api_requests_total{endpoint,method,status_code}` 承接，逐条留存的
埋点价值与之重叠——这是选择「丢」的依据。

**顺带修掉一个门禁盲点**：`tools/dev/env_inventory.py` 只扫 `os.getenv("X")` 这类**字面量**形态，
本次实现最初写成 `os.getenv(ACCESS_LOG_FULL_ENV)`（模块常量包一层）时，清单仍是 244 项、
`env-inventory` 门禁**照过**，该键对运维不可见。改为字面量读取后门禁立刻要求「登记进示例或
声明内部」，于是 `STP_ACCESS_LOG_FULL` 进了 `.env.example` 与文档的生成块（245 项）。该键在
代码里留了注释说明这一约束。

## Alternatives

- **`uvicorn --no-access-log`（方向 1，全关）**：放弃。会把非 2xx 一起关掉，而 4xx/5xx 的逐条
  记录是 access log 唯一不可替代的价值（指标只有计数，没有「哪个请求、什么路径」）。
- **2xx 采样或按比例保留**：放弃。采样让「我刚发的那个请求有没有到」变成不可回答，而逐端点
  排除是确定性的、可解释的。
- **handler 级挂载 filter**：放弃。uvicorn 的 handler 在它自己的 `dictConfig` 里创建，app 导入期
  未必已存在；logger 级挂载经实测能扛过后续 `dictConfig`。
- **方向 5（access log 独立文件 + 独立保留期）**：放弃。只把体积换个地方堆，不减少写入量，还多一套
  轮转配置要维护。
- **方向 4（压请求量：调 patrol-heartbeat / claim 的轮询频率）**：本 PR 不做。那是**行为改动**
  （影响租约/心跳语义），需要单独评估与灰度，混进来会让本 PR 的验证面不再单一。
- **把门禁的常量盲点也一并修（让 `env_inventory` 认常量）**：本 PR 不做，只在中立化写法上绕开并
  留注释。扫描器支持常量解析是工具面改动（要跨模块解析绑定），属另一个 Requirement。

## Verification

```
$ python -m pytest backend/tests/core/test_logging_setup.py -q
59 passed
  覆盖：7 条路径的 2xx 全丢（含带 query）／7 条路径的 4xx·5xx 全留／非轮询路径的 200 与
  相似路径 `/api/v1/heartbeat-detail` 不误伤／逃生阀 1 时一条不丢／args 形状异常（None、太短、
  status 非数字）与 uvicorn 改 dict 形态时 fail-open／install 幂等／filter 扛过 uvicorn dictConfig／
  真实 logger 端到端（挂捕获 handler 发三条，只应留下 500 与 plan-runs）／main.py 接线钉子
 变异自证：删掉 `status_code >= 400` 放行 → 30 failed；还原 → 59 passed

$ python tools/dev/env_inventory.py --check
[OK] 环境变量清单一致（245 个读取名）

$ python scripts/run_gates.py check:pr
[OK] check:pr (21 gates)   （含 env-inventory / ruff / eslint / tsc / knip / compileall /
                            layering / immutability / ip-leak / prom-alerts / agent-tests 等）
```

**未验证（如实标注）**：本机控制面进程持有旧代码，**降噪要等控制面重启才生效**；本次没有在生产
进程上跑过带流量的实测，量级预期（access 行减少 ≈87%，即 backend.log 总量再减约 20%）来自
#3020 的只读统计，不是本 PR 实测。

## Revisit

- **uvicorn 升级后回看 access 行是否重新刷屏**：形状判据失配时是**静默不降噪**（fail-open 的代价），
  这一点没有测试能替你发现——升级后按 `logs/backend.log` 的 access 行占比复核一次。
- 名单里若某条路径从「高频内部轮询」变成低频/关键链路（例如 patrol-heartbeat 被改成秒级事件面），
  应把它从 `ACCESS_LOG_NOISE_PATTERNS` 移除。
- 若要进一步压体积，走 #3020 的方向 4（调轮询频率/退避）——那需要独立评估租约与心跳语义。
- `env_inventory` 的常量盲点值得单独立单：任何「用常量包 env 名」的新读取点都会从清单与文档里消失。
