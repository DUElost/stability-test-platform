# ADR-0035: Agent 主机身份与凭据体系

- **状态**：Proposed（草案供拍板——裁决前不动代码；#906 不随本 ADR 关闭，实施单另行拆分）
- **优先级**：P1
- **日期**：2026-09-08
- **决策者**：平台研发组 / 架构组
- **标签**：Agent, 身份认证, 凭据管理, 横向扩展
- **来源**：R02 台账 #910 的 R02-R01（设计风险，P1）；Epic #720 / #46
- **关联**：ADR-0027（控制面水平扩展）、ADR-0024（浏览器会话）、#905（已修的 nfs_path 越界——同一信任面）

## 1. 背景与问题

当前**所有 Agent 共用一个全局 `AGENT_SECRET`**，且主机身份由客户端自报：

- Socket.IO `AgentNamespace.on_connect`：验证全局 secret + 接受客户端提供的
  `host_id`（`backend/realtime/socketio_server.py`）；
- Agent API 各端点：同款共享 secret 模式（`backend/api/routes/agent_api.py`）；
- `AGENT_SECRET` 部署在**每台设备**的 Agent 安装目录（暴露面 = 全部设备）。

后果链：单个 Agent 凭据泄露（设备被物理接触 / 安装包被抽取 / 日志泄漏）→
攻击者可**冒充任意其他主机**——顶替别的设备接入 Socket.IO 接任务、上报伪造
结果、抢占 claim。现有防护的边界：fencing/租约能限制「过期执行者」继续操
作，但**不能**识别「持有效全局 secret 的冒充者」；网络边界（internal 无 TLS
内网，ADR-0024 v1.1）缓解外部攻击者，不缓解已在内网设备上的横向移动。

这是一张**设计风险**票，不是已证实故障——定级 P1 的原因是它位于「设备集群
信任根」的位置，且修复窗口与 Agent 安装链路耦合（越晚改越贵）。

## 2. 目标与非目标

**目标**：
- 单台主机凭据泄露不能冒充其他 host（#906 验收）；
- 主机身份在 Socket.IO 握手、Agent API、claim 三条通道上口径一致；
- 安装/重装/换机流程可自动化（agentctl / ansible 链路不引入人工发凭据）。

**非目标**：
- 不解决控制面到 Agent 方向的命令真伪（现状由 Socket.IO 会话粘性保证，
  ADR-0027 P3-2 文档诚实边界）；
- 不改变浏览器侧用户认证（ADR-0024 管辖）。

## 3. 方案对比

### 方案 A：每主机独立凭据（host 表存哈希，安装期下发）

控制面为每台 host 生成独立 secret；`hosts` 表存哈希（不存明文）；安装工具
（agentctl / ansible playbook）在注册时领取该主机的凭据并写入 Agent 本地
配置；Agent 握手/API 带 `host_id + host_secret`，服务端按 host 查哈希校验。

- ✅ 直接满足「泄露单机不冒充他机」；轮换可按主机粒度（单机泄露 → 只轮那台）；
- ✅ 复用现有 host 注册流（host 表已有注册/心跳生命周期）；
- ⚠️ 安装链路改造：agentctl 需增加「向控制面领取凭据」步骤（引导期需要一次
  管理员侧授权——见方案 C 的引导问题）；离线安装场景需要凭据预生成。

### 方案 B：mTLS 客户端证书绑定主机

每台 host 签发客户端证书，Nginx/控制面校验证书指纹 ↔ host 绑定。

- ✅ 最强形态（凭据不出设备、防复制能力最好）；
- ❌ 引入 CA 与证书轮换运维（设备量大时成本显著）；TLS 终止点与 FastAPI 的
  身份传递（header 注入）需要 #46 的 HTTPS 前置——当前 internal 是无 TLS 内
  网部署（ADR-0024 v1.1），**前置依赖不满足**；
- 结论：作为 #46 TLS 落地后的演进方向，不是当下方案。

### 方案 C：全局引导 secret + 服务端注册质询（首启换发主机 token）

Agent 首次连接用全局引导 secret 走「注册」：服务端生成该 host 的一次性
注册凭据，管理员在 UI 批准（或预登记 host 指纹），换发**主机专属 token**
（方案 A 的凭据，只是下发时机从安装期改为首启握手）。全局 secret 之后仅用
于新设备引导，不再授权常规操作。

- ✅ 兼容现有安装包（不需要在安装期注入每机凭据）；把「泄露全局 secret」的
  危害从「冒充任意 host」缩到「引导新设备注册（可被审批拦截/发现）」；
- ⚠️ 需要注册审批 UX 与 token 生命周期管理（续期/吊销/重装重发）。

### 方案 D：接受现状，文档化风险边界

维持全局 secret，在 ADR-0027/运维文档写明信任模型（internal 网络边界 +
fencing 兜底），挂 #46 TLS 后复议。

- ✅ 零成本；❌ 与 P1 定级矛盾——凭据暴露面（全部设备）与「设备可被物理
  接触」的现实使风险边界很难自洽成文。

## 4. 推荐裁决

**采纳 A 为目标形态，C 为引入路径**：

1. host 表增 `agent_secret_hash`；握手/API 校验改为「按 host 校验其专属凭
   据」；
2. 首启引导走 C 的注册质询（引导 secret → 管理员批准 → 换发主机凭据），
   避免安装链路一次性大改；
3. 全局 `AGENT_SECRET` 退役为「仅引导」，轮换 runbook 同步收窄其影响面；
4. 轮换：按主机粒度可轮（单机泄露只动那一台）。

## 5. 影响面（裁决后拆实现单）

- `backend/realtime/socketio_server.py` AgentNamespace 握手校验；
- `backend/api/routes/agent_api.py` 全部端点鉴权；
- host 注册/审批 UI + `hosts` 表迁移（凭据哈希列）；
- `backend/agent/agentctl` 与 ansible playbook 的引导流程；
- 轮换 runbook（`docs/operations/`）与 `AUTO_MERGE_PAT` 类似的 secret 管理
  纪律（凭据不入会话/日志）。

## 6. 决策请求

需要裁决：采纳「A 目标 + C 引入路径」的推荐，还是选择 B / D；以及实施
时点（是否与 #46 HTTPS 同批）。裁决后本 ADR 转 Accepted 并拆实现单。
