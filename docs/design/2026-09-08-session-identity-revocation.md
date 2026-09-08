# R02 会话身份与撤销统一设计（#900–#903 / #909）

- **状态**：Proposed（B1-C 排期交付；B2 实现依据——B2 开工前如无异议按此实施，修改意见落在 [#910](https://github.com/DUElost/stability-test-platform/issues/910) 排期评论）
- **日期**：2026-09-08
- **基线**：`a9f1ceaa`（PR #998 合入后 main）
- **来源**：R02 认证、授权与安全边界审查台账 [#910](https://github.com/DUElost/stability-test-platform/issues/910)（静态只读审查 2026-09-07）；台账修复顺序建议「会话身份与撤销宜统一设计、分别验证」
- **覆盖**：#900（JWT sub 可复用）、#901（/refresh 不消费旧 token）、#902（改密/重置不失效会话）、#903（Dashboard/metrics 绕过停用检查）、#909（ENV=internal 契约裁定）
- **不覆盖**（各自独立单）：#904（Socket.IO CORS）、#905（nfs_path）、#906（AGENT_SECRET 绑主机，需 ADR）、#907（可归责审计）、#908（SSH keyscan）

## 1. 现状缺陷链（证据见各 issue 与台账，此处只列结构）

同一用户身份在代码里有**三个校验面、两种强度**：

| 校验面 | 现状检查 | 缺陷 |
|---|---|---|
| REST `get_current_user`（auth.py:157） | 签名+type+exp → 按 username 查库 → `is_active=="Y"` | sub=username 可复用（#900）；无会话纪元（#902） |
| `metrics.verify_metrics_access`（metrics.py:34） | **仅** `decode_token`（签名+type+exp） | 停用/删除用户 token 到 exp 前全通（#903） |
| Socket.IO `/dashboard` 握手（socketio_server.py:364） | **仅** `decode_token` | 同上（#903）；静态 ws token 分支除外 |

撤销模型：jti 黑名单只在 `/auth/refresh` 与 `/logout` 消费；**`/refresh` 成功路径不吊销已消费 jti**（#901，7 天窗口内无限重放）；改密/重置密码（users.py:146/:279）不影响任何在发 token（#902）。身份基底：`sub=user.username`（auth.py:152-153），username 是可复用业务键——删建同名账户即冒充（#900）。

## 2. 统一机制（四条决策）

### D1 身份基底：`sub` = 不可变 PK

- access/refresh 签发改为 `{"sub": str(user.id), "username": user.username, ...}`；**查找一律按 id**（`int(sub)` 解析失败即 401）；`username`/`role` 降级为**信息性 claim**（日志与展示），鉴权决策只信 DB 行（`require_admin` 现状已读 DB role，保持）。
- 依据：username 可复用（删除后重建同名=新身份），PK 不可复用——这是 #900 的根因修复，不是换一个查找键。

### D2 会话纪元：`User.token_version`

- 新列 `token_version INTEGER NOT NULL DEFAULT 1`（alembic 迁移 + 回填）。
- access/refresh 签发携带 `ver = user.token_version`；**校验时与 DB 值比对，不等即 401**。
- **递增时机**（全量失效该用户所有在发 access+refresh）：
  1. 自改密（users.py:279 路径）；
  2. admin 重置密码（users.py:146 路径）；
  3. admin 停用/启用（`is_active` 变更）——停用本被 active 检查拦截，bump 使**重新启用后旧 token 仍失效**，语义闭合成「纪元不连续即不可延续」；
  4. admin 改 `role`——使 token 内 role 快照立即作废。
- 效果：#902 的「改密后已有会话」即时失效，access 无需等待 exp。

### D3 单一校验面：`authenticate_access(token, db) -> User`

- 抽一个公共函数（落点 `backend/core/security.py` 或 `services/auth_session.py`，实现单定）：`decode(expected_type="access")` → `sub` 解析 id → 查库 → `ver` 比对 → `is_active=="Y"` → 返回 `User`；任一步失败 401/None。
- **三个校验面全部改为调用它**（D3 是本设计的强制力所在：不允许存在第三种强度）：
  1. REST `get_current_user`（行为不变，代码收敛）；
  2. `verify_metrics_access` 的 Bearer 分支；
  3. Socket.IO `/dashboard` 握手的 JWT 分支（静态 ws token 分支不变——机器对机器，非用户身份）。
- 成本：REST 现状已每请求查库，增量仅 metrics/socket（低频握手）。Socket.IO 握手在 sync 上下文——复用现有 sync sessionmaker，实现细节归单。
- **已知边界**：Socket.IO 长连接握手通过后，bump 不会主动断开存量连接（直到断线重连重新握手）；「服务端主动踢下线」需要断连机制，超出本批范围，见 §6。

### D4 refresh 轮换：消费即吊销

- `/auth/refresh` 成功路径：先 `revoke(旧 jti, reason="rotation")`（复用 logout 的黑名单写路径），再签发新对并 set cookie；同事务提交审计（沿用 #281 纪律）。
- 重放已消费 token → 既有 `is_revoked` 检查 → 401 + `refresh_rejected` 审计——#901 关闭。
- `logout` 语义不变（吊销 presented jti + 清 cookie）；「全设备登出」由 D2 的 bump 覆盖，无需逐 jti 追踪。

## 3. 兼容与迁移

- **硬切换，无宽限**：无 `ver` claim 的存量 token 一律拒绝（等价部署后全员重新登录一次）。理由：存量 token 正是 #900 冒充窗口的载体，宽限期=保留攻击面；用户侧成本为一次重登录，前端 401→登录跳转已存在。
- DB：加列 + 回填 1 的常规迁移；`pr-migrate-empty-db` 天然覆盖空库路径。
- API 形状零变化（cookie 名、端点、响应体均不动）；`/auth/token` 发出的 bearer token 同样携带新 claims。

## 4. #909 裁决：ENV=internal 两级生产姿态

**裁决：代码现状正确，契约文本修订。**（#909 本体是 docs/tech-debt。）

- 依据：`ENV=internal` 是「无 TLS 内网部署」标识；Secure cookie 在纯 HTTP 下被浏览器直接拒发，强制它等于必然拒启且无安全收益（security.py:76-81，#281 部署决策在案）。internal 已保留其余全部生产级护栏：CSRF 强制、SameSite 取值校验且拒绝 none、注册默认关闭、SocketIO 强制认证。
- **修订内容**（实现归 B2 尾部纯 docs 单）：
  - AGENTS.md 硬不变量改为两级表述：「`ENV=production` 必须满足 secure cookie；`ENV=internal`（无 TLS 内网）豁免 Secure，但必须保留受限 SameSite、CSRF guard 与注册关闭；上 TLS 后必须置 `AUTH_COOKIE_SECURE=1`」；
  - ADR-0024 增补 internal 层级条款（引用本节为决策依据）。
- **退出条件**：internal 部署上 TLS 后必须收紧 Secure=1（与代码注释既有约定一致）；届时是否合并两级再议。

## 5. B2 实现拆单（串行，同域文件）

| 单 | issues | 内容 | 主要文件 |
|---|---|---|---|
| A | #900 + #901（合单，排期已定案） | D1 sub=id + D4 rotation + §3 硬切换 | core/security.py、api/routes/auth.py |
| B | #902 + #903 | D2 token_version 迁移 + D3 authenticate_access 抽取与三面接入 + 各 bump 点 | users.py、metrics.py、socketio_server.py、迁移 |
| C | #909 | §4 契约文本修订 | AGENTS.md、ADR-0024 |

每单回归要求：停用后 token 立即失效 / 重放 refresh 被拒 / 删建同名账户旧 token 401 / metrics 与 socket 拒绝停用用户。A、B 可合并为一单评审（同簇），C 独立。

## 6. 风险与开放问题

- **多设备语义**：bump = 全设备登出。改密/停用场景这正是需求；「登出单设备」需要 per-session jti 登记表，超范围（若需要，另立设计）。
- **Socket.IO 存量连接**：见 D3 边界——依赖断线重连收敛，不做主动踢除。
- **token_version 查询开销**：REST 每请求本就查库（现状），无增量；metrics/socket 为低频面。
- **DB 可用性耦合（#1041 成文）**：D3 使 metrics Bearer 分支与 dashboard 握手依赖 DB；DB 故障时**显式失败（fail-closed）**——metrics 返回 5xx、握手以 `ConnectionRefusedError` 拒绝，不降级到签名级放行（降级 = 重开 #903 停用旁路）。X-Agent-Secret 的 metrics 认证分支不查库、不受影响。dashboard 握手认证经 `asyncio.to_thread` 在工作线程执行，不阻塞 Socket.IO 事件循环（#1041）。
- **`username` claim 的展示用途**：前端 `/auth/me` 走 DB，不读 claim；claim 仅服务端日志。若未来前端要读，需文档化「信息性、不权威」。
