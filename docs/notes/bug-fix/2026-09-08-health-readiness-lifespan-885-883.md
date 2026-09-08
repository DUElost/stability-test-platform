# /health readiness 化 + lifespan 清理异常安全（#885/#883，R01-F05/F03）

Status: implemented
Class: bug-fix

## Decision

**#885（R01-F05）——/health 从「DB 通则 healthy」改为 readiness 探针**：

1. `/health`（readiness）：DB 断开 → 503 `DB_UNAVAILABLE`（既有）；Redis 不可达
   → 503 `REDIS_UNREACHABLE`（lifespan 已建 `redis_client` 时 ping 验证）；
   SAQ 未就绪 → 503 `SAQ_NOT_READY`（inprocess 与 producer 模式同判，ADR-0026
   P0 pump 就绪 = worker 活跃或 producer 已连）。`STP_SKIP_INFRA_CHECK=1`（非
   生产类环境）与 lifespan 同条件跳过基础设施检查；`TESTING=1` 下 lifespan 不
   启动 Redis/SAQ，检查同通道跳过（测试环境语义保持）。
2. 新增 `/health/live`：纯 liveness——进程在即 200，不做依赖检查（编排层需要
   「不因依赖抖动重启」的存活判断时指向它）。
3. Dockerfile.backend HEALTHCHECK 保持指向 `/health`——语义翻转后自动获得正确
   行为，附注释说明两探针分工。此前 worker 退出/Redis 断连时 HEALTHCHECK 仍报
   健康正是本缺陷。

**#883（R01-F03）——lifespan 异常安全**：

1. **依赖校验先行**：Redis PING + SAQ 启动移到 Scheduler 之前——此前 Scheduler
   先起、校验在后，校验失败会留下已启动的 Scheduler 且裸 `yield` 后的清理段
   不执行；
2. **try/finally + 提取 `_lifespan_cleanup`**：启动期任何异常 → 清理已启动
   资源后重抛；关闭期每步独立 try/except（RunConsole/pump 撤销/SAQ 停止/
   Scheduler 退出/Redis 关闭/引擎 dispose），单步失败不阻断后续清理；
3. Scheduler 半启动回滚：`__aenter__` 失败不再调 `__aexit__`；register/start
   失败则 `__aexit__` 回滚（其自身失败仅记日志，不吞原始异常）。

## Alternatives

- **保留 /health 纯 liveness、新增独立 /health/ready**——放弃：Docker
  HEALTHCHECK 已指向 /health，翻转语义即修复误报；新增 live 端点补齐另一半，
  免改镜像层探针配置；
- **readiness 里逐项查 Redis/SAQ 而不复用 `is_saq_ready()`**——放弃：pump
  就绪标记已有权威判定（ADR-0026 P0），重复实现只会漂移；
- **lifespan 拆成多个 async contextmanager**——放弃：清理顺序与部分失败语义
  在单函数内更直观，`_lifespan_cleanup` 提取已达到可测目标（4 条直接单测）。

## Verification

- 新增 `test_main_lifespan.py` **4 用例**：Redis 校验失败 → Scheduler 工厂
  未被调用（校验先于副作用）+ 清理执行；Scheduler `__aenter__` 失败 → SAQ
  停止 + Redis/引擎清理；关闭期 SAQ stop 抛错 → Scheduler/Redis/引擎清理继续；
  Scheduler `__aexit__` 抛错 → Redis/引擎清理继续；
- `test_health_saq.py` 重写到新契约 **9 passed**：TESTING 跳过语义、SAQ 未就绪
  503（inprocess+producer 双模式）、Redis ping 失败 503、skip_infra 豁免、
  `/health/live`；
- 全量 `backend/tests/api` **895 passed**；`check:quick` 7 门禁全绿。

## Revisit

- 编排文档（deploy 模板）如引用 /health 语义需同步「readiness」表述；
- `STP_SKIP_INFRA_CHECK` 在生产类环境被 lifespan 忽略（既有护栏），readiness
  同判——生产类部署无法豁免 readiness 的 SAQ/Redis 检查（有意）。
