# Agent 主机级凭据边界 ADR 定案（#906）

Status: implemented
Class: process

## Decision

新增 **ADR-0035（Accepted v1.0）**：正式裁决现阶段继续接受全局共享
`AGENT_SECRET` 作为主机级认证原语（#906 验收标准①），并把泄露后的可冒充面、
升级为主机级凭据的触发条件与设计骨架成文；验收标准②（泄露单机凭据不能冒充
其他 host）明示 deferred，触发条件满足即按骨架启动（无需新 ADR）。

关键内容：威胁模型=受信管理域（ansible/vault 纳管、ADR-0024 v1.1 TLS 边界）；
冒充面收窄=作业级 fencing token（ADR-0019/#992/#1073）+ SID registry 单 owner
CAS（#881/#887）+ 审计；四条升级触发（泄露事件/跨不受信网段/R06·R08·R11 P0
结论/主机级审计归属合规）；骨架=Host 凭据列+校验收敛单 helper（socket DB 读
fail-closed，#1041 先例）+ per-host 分发 + auto-register 改 enrollment token +
metrics/notifications 保持 ops token 分界 + 双值宽限轮换。

## Alternatives

- 立即实施主机级凭据：泄露未发生、受信域假设成立，全网轮换+离线宽限的
  风险收益不匹配 → 不做（触发条件已成文）。
- mTLS/客户端证书：最强绑定但与 #46 HTTPS 硬化同轨，超出本 tech-debt P1。
- vault 按主机分发不落库：事实易漂移、不支持自动上架 → 否。

## Verification

- 新建 `docs/adr/ADR-0035-agent-secret-boundary.md`；README「当前 ADR 清单」+
  M7 行、DOC-MAP「架构 ADR」行同步（S12 索引一致性）。
- `scripts/run_gates.py check:quick` → gov-surface S1–S12 全绿（含 S12 ADR
  索引/版本 token 检查）。

## Revisit

- 触发条件任一命中即自动重启实施（ADR-0035 内已写明），无需再开 ADR；
- #46 HTTPS 落地后重评 mTLS 与受信域假设是否收窄。
