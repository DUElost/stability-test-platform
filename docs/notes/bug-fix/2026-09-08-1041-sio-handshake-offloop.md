# #1041 修复：dashboard 握手认证出事件循环 + metrics DB 故障语义成文

Status: implemented
Class: bug-fix

## Decision

#1020（D3 三面收敛）给 Socket.IO dashboard 握手引入 sync DB 查询后，查询落在
事件循环线程上执行（`AsyncNamespace.on_connect` 为 coroutine）。修复分两半：

1. **握手认证出循环**（`backend/realtime/socketio_server.py`）：抽出模块级
   `_authenticate_dashboard_user(token)`（sync `SessionLocal` +
   `authenticate_token`，与 REST/metrics 同一校验面不变），on_connect 经
   `await asyncio.to_thread(...)` 调用——与仓库既有 sync-in-async 惯用法一致
   （agent 侧 `asyncio.to_thread` 已在用），不新增 async 版校验面、不在两层
   重复业务逻辑（#517 纪律）。`SessionLocal`/`authenticate_token` 从函数内
   inline import 上移到模块顶部（依赖子树与既有顶部 import 相同，无环）。
   try/except 结构保持：任何 DB 异常仍转 `ConnectionRefusedError`（fail-closed）。
2. **metrics DB 故障契约成文**（设计 note §6 增补）：D3 后 metrics Bearer 分支
   与 dashboard 握手依赖 DB，DB 故障时显式失败——metrics 5xx、握手拒绝；不
   降级到签名级放行（降级 = 重开 #903 停用旁路）。X-Agent-Secret 分支不查库
   不受影响。代码即契约（现状行为），§6 落文字。

## Alternatives

- **async 化 authenticate_token（AsyncSessionLocal + async 变体）**——放弃：
  需要在 sync/async 双栈复制校验面业务逻辑，违反 #517「禁止在两层重复业务
  逻辑」；`to_thread` 已消除事件循环阻塞，async 化收益仅剩线程占用，留给
  #517 域迁移统一裁决。
- **metrics DB 故障降级/短缓存**——放弃：降级放行重开 #903；短缓存引入
  撤销延迟语义（停用用户在 TTL 内仍可读 metrics），与 #902/#903 的「立即
  失效」目标冲突，且 scrape 为低频运维面，显式失败语义最简单可审计。
- **on_connect inline import 维持原样仅包 to_thread**——放弃：函数内 import
  是 #1020 的最小 diff 产物，本就是 anti-pattern；上移后 monkeypatch 点与
  模块命名空间一致，可测。

## Verification

- 新增不变量回归 `test_dashboard_token_auth_runs_off_event_loop`：monkeypatch
  模块级 `SessionLocal`/`authenticate_token` 记录执行线程 ident，断言与事件
  循环线程不同——回归为循环内直跑 sync 查询时必红；
- `pytest backend/tests/realtime/ backend/tests/api/test_metrics_auth.py
  backend/tests/api/test_auth_cookie_session.py` **91 passed**（含全部 #903/
  #904/D2 既有行为回归，行为语义零变化）；
- `python scripts/run_gates.py check:quick` 全绿（见 PR 检查）。

## Revisit

- metrics/socket 的 async session 统一归 #517（deferred）域迁移，本单只收
  「不阻塞事件循环 + 契约成文」两点；
- 若未来引入连接池耗尽类压测，`to_thread` 的默认线程池容量（min(32, cpu+4)）
  是握手吞吐上界，届时按实测调；
- 静态 ws token 分支不查库、不经线程池，行为不变。
