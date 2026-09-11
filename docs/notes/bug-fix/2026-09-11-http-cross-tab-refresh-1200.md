# HTTP 非安全上下文跨标签 refresh 已知限制实测与裁定（#1200 / R12）

Status: implemented
Class: bug-fix

## Decision

本质问题（#1200）：#1039 依赖 Web Locks 串行化多标签 refresh，但 Web Locks 仅
**安全上下文**可用——`ENV=internal` 无 TLS 部署（ADR-0024 v1.1 例外场景）下
`navigator.locks` 不存在，refresh 退化为单标签 `_refreshInFlight` 防抖，跨标签
并发竞态是否复现需实测确认。

实测（隔离环境，Playwright 双标签同时调用产品代码 `refreshAccessToken`，服务端
1s 延迟放大 in-flight 窗口）：

- 安全上下文（`http://127.0.0.1`）：`locks=true`，两请求**串行**（maxOverlap=1）
  → #1039 生效；
- 非安全上下文（`http://<LAN-IP>` 纯 HTTP）：`locks=false`，两请求**并发**
  （maxOverlap=2）→ **fail-open 确证**。

裁定（2026-09-11 用户裁决）：**接受限制并文档化**，不做前端自建跨标签锁：

- ADR-0024 升 **v1.2**（新增 v1.2 节记录实测与限制）；
- 缓解 = internal 部署下的单标签使用纪律；随 v1.1 复议触发器（#46 TLS）
  落地自动消除；
- 无代码改动 → 不回退 #1039 在安全上下文的修复（验收第 3 条天然满足）。

## Alternatives

- **BroadcastChannel 尽力串行化**——放弃：BC 无原子性、非严格互斥，需自建
  通告/等待协议，引入新失败模式（消息丢失/延迟/死等）而仅缩小竞态窗口；
- **服务端 rotation 宽限期（容忍并发 refresh）**——超出 R12 前端范围（属后端
  会话域）；如再复发可作为独立提案评估；
- **localStorage 锁 / 引入 polyfill 库**——放弃：Web Storage 无原子 CAS，
  同类非严格互斥且新增依赖，复杂度不低于自建。

## Verification

实测环境与方法（隔离，零生产影响）：

- harness = vite dev（15173 端口，host 全网卡）+ Playwright（本机缓存 chromium）
  打开两个标签访问 harness 页面，页面直接导入产品代码
  `frontend/src/utils/auth.ts` 的 `refreshAccessToken`；
- `/api/v1/auth/refresh` 由 Playwright route 拦截：延迟 1s 响应并记录每请求
  `[start, end]`，按时间窗重叠计算 maxOverlap（并发度上界）；
- 场景 A `http://127.0.0.1:15173/h1200.html` →
  `{"secure":true,"locks":true,"posts":2,"maxOverlap":1}`；
- 场景 B `http://<LAN-IP>:15173/h1200.html` →
  `{"secure":false,"locks":false,"posts":2,"maxOverlap":2}`；
- 两场景各 2 个 POST（后到标签在锁释放后仍会 refresh，串行且由赢家 cookie
  兜底；差异仅在并发性）；
- `check:quick` → 7 gates 全绿（含 gov-surface S12 ADR-0024 头部 ↔ README
  主表版本一致性）。

未完成（pending）：

- 真实 internal 部署（Nginx 反代 + 真 cookie rotation）的双标签验证：本机为
  生产控制面宿主，不在生产部署上构造多标签 401 故障；隔离结论已足以确认
  机制（locks 缺失 → 并发）。

## Revisit

- #46（HTTPS 硬化）落地后复跑同 harness，确认 `locks=true` 恢复串行；
- 若 internal 部署出现真实跨标签 refresh 事故（用户被登出/会话丢失），将本项
  升级为代码修复——优先服务端 rotation 宽限期，而非前端自建锁。
