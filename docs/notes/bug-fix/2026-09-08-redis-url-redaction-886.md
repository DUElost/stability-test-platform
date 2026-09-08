# Redis URL 日志凭据脱敏

Status: implemented
Class: bug-fix

## Decision

#886（R01-F06）：`REDIS_URL` 可能携带密码，控制面启动成功日志与 Redis 连通性
失败异常不得原样传播完整 URL。将 Socket.IO 模块内的私有脱敏实现提升为
`backend.core.redis.redact_redis_url`，并统一用于三处：

1. `backend.main` 的 `redis_ping_ok` 成功日志；
2. `backend.tasks.saq_worker.verify_redis_connectivity` 抛出的 `RuntimeError`；
3. Socket.IO Redis adapter 的启动日志。

Socket.IO 侧继续暴露 `_redact_redis_url` 名称作为既有 Agent 测试的兼容别名；
行为保持“只移除 password，不改变 URL 其余部分”。该函数只处理标准 Redis URL
的 userinfo 密码，不解析业务自定义 query 中的敏感字段。

## Alternatives

- 配置统一 logging filter：会依赖每条日志的消息格式或额外 context，漏接异常
  字符串的风险更高，也无法替代异常对象本身的安全边界。
- 各调用点继续复制私有 helper：会让新 SAQ 与 main 路径继续偏离，后续密码
  脱敏规则一旦调整仍可能漏改。

## Verification

- `venv/bin/python -m pytest backend/tests/core/test_redis_url.py backend/tests/api/test_health_saq.py backend/agent/tests/test_socketio_redis_adapter.py -q`
  ——15 passed；
- `venv/bin/python -m ruff check`（改动文件）——All checks passed；
- `git diff --check`——干净。
- `venv/bin/python scripts/run_gates.py check:quick`——7 gates passed
  （frontend 依赖复用本机同 lockfile 安装）。

## Revisit

- 若未来 Redis 配置允许在 query、TLS 材料或其他扩展字段中携带凭据，需扩展
  脱敏规则并同步测试；当前修复只覆盖标准 URL password。
