# ADR-0035: Agent 主机级凭据边界——共享 AGENT_SECRET 的风险接受与升级触发

- 状态：**Accepted（v1.0）**
- 版本记录：v1.0（2026-09-08 初版，#906）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-09-08
- 决策者：平台研发组
- 标签：认证, Agent 身份, AGENT_SECRET, 主机绑定, 安全边界
- 关联：R02 审查台账行 R02-R01（#906）；ADR-0024（浏览器会话安全）、ADR-0027（SID registry）、ADR-0019（device lease/fencing）；#900/#902/#903（人类会话 token_version 纪元，本 ADR 不覆盖）

## 背景

`backend/realtime/socketio_server.py`（`/agent` namespace）、`backend/api/routes/agent_api.py`
（`_verify_agent`，16 个端点）、`backend/api/routes/auth.py`（`verify_agent_secret`，
heartbeat/logs/notifications）、`scripts.py`（`_try_verify_agent`）、`metrics.py`
（可选分支）五处校验共用**同一个全局 `AGENT_SECRET`**：全部 Agent 主机持同一值，
`host_id` 由客户端在 socket auth 与 REST body 中自报，控制面不做 secret↔host 绑定
（`agent_api.py:2752-2755` 注释明示该语义）。部署侧（ansible group_vars、install_agent.sh、
host_updater 热更新）同样按单值分发；`Host` 表无任何主机级凭据列。

风险（#906 原文）：单个 Agent 凭据泄露即扩大到其他主机；泄露者可冒充任意 host_id
接入/上报/拉取。R02 台账归类为「设计与既有风险」（非已确认缺陷），P1。

人类会话侧已完成身份纪元化（#900–#903、ADR-0024 族），与本 ADR 范围互斥：
`docs/design/2026-09-08-session-identity-revocation.md` 明示 #906「需 ADR」单独裁决。

## 决策

**现阶段继续接受全局共享 AGENT_SECRET 作为主机级认证原语**（Accepted），不引入
主机级凭据；把升级为主机级身份的条件与设计骨架在本 ADR 成文，条件满足即按
骨架启动实施。本 ADR 即 #906 验收标准①（ADR 明确风险边界）；标准②（泄露单机
凭据不能冒充其他 host）在触发条件满足前**不实施**，属明示 deferred。

### 接受边界（威胁模型）

- 控制面与 Agent 主机同处受信管理域：主机由 ansible/SSH 集中纳管（host 级 SSH
  凭据、vault 均强于共享 secret），控制面 ENV=internal/production 的 TLS 边界见
  ADR-0024 v1.1。攻击者拿到 `AGENT_SECRET` 的前提（已控一台受管主机或已读控制面
  env）本身已越过该边界的主要防线。
- 泄露后的可冒充面被既有机制收窄：
  - **作业写入**：claim/上传等作业级授权绑定 `fencing_token`/`agent_instance_id`
    （ADR-0019、#992/#1073/#1005/#1006），非「有 secret 即可写」。
  - **连接顶替**：SID registry 单 owner CAS（#881/#887）使冒充连接会 unregister
    真连接——冒充是**可观测的 DoS/事件**，不是静默数据面接管；审计与告警可发现。
  - **下行触发**：`scan_now` 等 control 走 socket room `agent:{host_id}`，需要
    先建立合法身份连接，同样落在上述 CAS/告警面。
- 剩余未收窄暴露：泄露者可冒充任意 host 上报心跳/日志/event、拉取脚本目录等
  **只验证 secret 不验证 host** 的读接口；以及全量轮换（热更新改全局 secret）
  前的旧值窗口。此暴露在受信域假设下判为可接受。

### 升级为主机级凭据的触发条件（满足任一即启动，按下方骨架实施）

1. 发生或疑似单机 `AGENT_SECRET` 泄露事件，且评估认为「全量轮换前旧值窗口内
   冒充读接口」会造成实际损害。
2. 部署扩展到不受信网段 / 多云 / 多租户，受信域假设失效。
3. R06（派发/执行）、R08（脚本库/外部工具）、R11（realtime 基建）审查出现
   「主机冒充导致不可接受后果」的 P0 场景（#906 交接区）。
4. 主机级审计归属（「谁上报了 X」）成为合规/定责要求。

## 主机级方案骨架（触发后按此实施）

- **存储**：`Host` 增 `agent_secret_hash`（带盐慢哈希）+ `credential_version`，
  alembic 迁移；admin 侧 hosts API 签发/吊销（沿用既有 admin auth，先例
  `boot_id`/`last_agent_instance_id`）。
- **校验收敛**：新增单一 helper `resolve_expected_secret(host_id)` + `compare_digest`，
  替换五处对全局 env 的比较；socket `on_connect` 的 DB 读经 `asyncio.to_thread`
  且 fail-closed（#1041 先例）；REST 各端点本身已按请求查库。
- **通道划分**：socket `/agent`、agent_api、heartbeat、logs、device-log-events 走
  主机级凭据；`metrics.py` 的 X-Agent-Secret 可选分支与 notifications webhook 保持
  独立 **ops token**（不经 host 凭据），本 ADR 定下该分界，实施时不得并入主机凭据。
- **分发**：install_agent.sh / ansible group_vars / host_updater 热更新改为按 host
  下发；保留热更新通道但 per-host。
- **auto-register**：`host_id=0` 自动注册路径改一次性 enrollment token（admin
  签发、带 TTL 或单次使用），不再允许凭「全局 secret + host_id=0」自举。
- **轮换与离线**：离线主机收不到新 secret → 支持双值宽限窗口（旧值+新值并存期）
  或 agent 心跳拉取式轮换；先经 agent 版本闸门（`agent_version_gate`）保证
  全网版本支持后再强制切换。

## 影响面

- 当前形态零运行时改动：五处校验、env 模板、ansible、install/hot-update、
  全部相关测试维持共享值语义（`test_agent_secret_guards.py`、
  `realtime/test_agent_rpc.py`、`api/test_metrics_auth.py` 等）。
- 触发实施时的影响面即「主机级方案骨架」各节；预计含迁移、校验收敛、
  分发链路、注册协议与测试大面更新，需独立批次 + Agent Note + 逐步灰度。

## 替代方案

- **mTLS / 客户端证书**：主机绑定最强（密码学绑定 host），但需 CA、ingress/反代
  改造、证书生命周期管理，与 #46（HTTPS 硬化）同轨；不在本 tech-debt P1 范围。
- **vault 按主机分发、控制面不落库**：避免 DB 迁移，但 vault 与控制面事实易漂移、
  不支持自动上架主机；校验侧仍要 host→期望值查找，省不掉骨架里的 helper。
- **立即实施主机级凭据**：本次不做——泄露事件未发生、受信域假设仍成立，且
  实施涉及全网主机轮换与离线宽限，风险收益不匹配（决策见上）。

## 复议

- 触发条件任一命中即自动重启本决策（按骨架实施），无需再开新 ADR；
- #46（HTTPS 落地）后重评 mTLS 与 production 边界是否收窄受信域假设。
