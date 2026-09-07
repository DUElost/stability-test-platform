# internal 无 TLS 部署的 Secure cookie 例外契约化

Status: implemented
Class: architecture

## Decision

#909（R02-R03，PROJECT_REVIEW_PLAN 评审线）指出：`ENV=internal` 豁免
`AUTH_COOKIE_SECURE` 启动强制（`backend/core/security.py`）与 AGENTS.md 硬
不变量「生产环境必须满足 secure cookie」表述不一致。裁定采纳其验收标准第一项
——例外保留、边界显式化：

- ADR-0024 升 v1.1（文末修订节）：例外适用边界 = 无 TLS 内网部署（内网网络
  边界受控前提）；例外仅覆盖 Secure 启动强制一项，SameSite 受限与 CSRF guard
  在 internal 照常强制；复议触发器 = #46 TLS 落地后收窄。
- AGENTS.md 硬不变量行改写为「生产类环境（production 与 internal）……唯一
  例外是 internal 无 TLS 内网部署豁免 Secure 启动强制（ADR-0024 v1.1……）」；
  S11 锚串片段「secure cookie、受限 SameSite 和 CSRF guard」原样保留在场
  （gate 代码零改动）。
- adr/README 主表行摘要改 `v1.1：` 前缀（S12 头部 ↔ 版本记录块 ↔ 主表行
  一致性）。

裁定依据：代码注释与 #281 部署决策（2026-08-16，操作者选定）已论证「Secure
cookie 在纯 HTTP 下被浏览器拒发，强制 = 必然拒启且无安全收益」；取消例外
（#909 验收第二项）会立即拒启现网 internal 部署，且其前提（#46 HTTPS 硬化）
尚未落地——故作为复议触发器挂起而非当前选项。

## Alternatives

- 取消例外并要求 TLS + Secure（#909 验收第二项）：当前放弃——internal 现网
  无 TLS，立即生效等于必然拒启；依赖 #46 先行，改为 ADR-0024 v1.1 复议
  触发器。
- 只改 ADR-0024 不动 AGENTS.md：放弃——#909 的矛盾点正是硬不变量表述未区分
  例外，只改 ADR 留一半不一致；S11「有意改写须同步锚点」路径已核实可行
  （锚串子串保留即在场）。
- 运行时收紧（internal 也强制 Secure 并提供配置逃生门）：放弃——超出本纯
  文档 PR 范围，逃生门形态属 #46 设计空间。

## Verification

- `gov-surface`（S1–S12，含 S11 锚点在场 + S12 ADR-0024 头部 v1.1 ↔ 版本
  记录块末 token ↔ README 主表行版本前缀一致）全绿；
- 行为固化证据（本次未改运行时）：`backend/tests/test_agent_secret_guards.py`
  的 `test_lifespan_requires_secure_auth_cookies_in_production` /
  `test_internal_allows_http_cookies_without_secure` /
  `test_internal_still_rejects_csrf_disabled` /
  `test_internal_still_rejects_invalid_samesite`；
- Registry dogfood：fix-909-internal-secure-cookie 全程登记（`--issue 909`
  在窗查重生效）。

## Revisit

- #46 落地（internal 可 TLS）后：收窄或取消豁免（ADR-0024 v1.1 复议触发器）；
- AGENTS.md 硬不变量行再改写时保持 S11 锚串同步纪律（gate 代码注释明文：
  改写措辞必须连锚一起改）。
