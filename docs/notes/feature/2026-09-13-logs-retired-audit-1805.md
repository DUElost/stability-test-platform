# #1805 矩阵 row 12：退役机日志尾读允许 + 审计

Status: implemented
Class: feature

## Decision

`POST /agent/logs`（`backend/api/routes/logs.py::query_agent_logs`）在取到 host 后、
SSH 之前，新增退役判据：退役主机（`retired_at IS NOT NULL`）**仍允许**尾读，但落一条
审计 `action="host_retired_log_tail"`。补 2 例测试（3 → 5）。

## 语义依据（ADR-0038 D-5 分类，非新语义）

ADR-0038 D-5 把控制面动作分两类（`ADR-0038-host-retirement-semantics.md:120-123`）：

- **执行/配置类**（热更新/安装/升级门禁/reload/watcher 切换）对退役主机**拒绝**；
- **数据回收类**（scan/archive/**日志尾读**）**允许但仅显式 admin 触发 + 审计 +
  `skipped_retired` 不虚报完整**。

矩阵 row 12（`ADR-0038:166`）把 `logs.py:244-262` 列入「Agent 自服务写面」。
③ 切片（#1898）已覆盖 scan/archive 的回收类语义，**未覆盖日志尾读**——本单补齐该面。

## 关键判断：为什么是「允许 + 审计」而非「拒绝」

日志尾读是**只读取证**动作，不是控制动作——退役机的日志正是「为什么退役/退役前
发生了什么」的取证来源，拒绝它会让退役机的事后诊断不可能。这与热更新/安装
（会改变退役机状态）被拒绝的理由恰好相反。故本单**不改放行集合**，只补审计。

## 为什么审计是 `strict=False`（非 fail-closed）

与退役/解除退役（D2，`host_retirement.py` 用 `record_audit(strict=True)`）不同：

- 那条路径**写状态**——审计是事件真源，写不进去就不许改状态（fail-closed）；
- 本路径**只读**——因审计表缺失而拒绝一次取证读取，收益小于代价。

故用默认 `strict=False`（缺表仅告警、不阻断尾读），并在异常时 `logger.warning`
留痕，不吞掉失败。文档字符串已写明该口径差异，避免后来者误以为不一致。

## 无用户身份：不伪造 user_id

该端点由 **agent secret** 认证（`verify_agent_secret`），**不是用户会话**——即使前端
也调用它（`frontend/src/utils/api/logs.ts:25`），请求亦不带用户身份。故审计只记
调用事实与目标主机（`host_id`/`log_path`/`lines`/`retired_at`），**不填
`user_id`/`username`**。

ADR 原文措辞是「仅**显式 admin** 触发」——但该端点在当前鉴权形态下**无法表达
admin 身份**（无用户上下文）。本单如实按「可表达的那部分」落地（审计），不宣称
已满足「仅 admin」；差异与解法记入 Revisit。

## Alternatives

- **拒绝退役机尾读** → 否决：与 D-5 分类冲突（尾读属回收类，ADR 明示「允许」）；
  且会切断退役机的事后取证，与退役语义（保留历史、可追溯）矛盾。
- **审计用 `strict=True`（fail-closed）** → 否决：只读动作用 fail-closed 会因审计
  表缺失而拒绝取证，代价大于收益；写路径才需要 fail-closed。已在 docstring 写明差异。
- **补 admin 鉴权以满足「仅显式 admin 触发」** → 本单不做：会**改变该端点的鉴权
  契约**（现为 agent secret，且前端以会话 cookie 调用，两者并存但均无 admin 断言），
  属鉴权面变更，需独立裁决而非在退役语义切片里顺手改。记入 Revisit。
- **在 `/logs/query`（用户面）也加审计** → 否决（本单范围）：矩阵 row 12 点名的
  是 `logs.py:244-262`（即 `/agent/logs`）；`/logs/query` 读的是控制面自身日志库
  （非 SSH 触达退役机），不产生「SSH 触碰退役机」这一事实，不在该行覆盖面内。
- **加 `skipped_retired` 计数** → 不适用：ADR 的 `skipped_retired` 是**扇出**语义
  （scan/archive 面向多主机，需报告「哪些因退役被跳过、不虚报完整」）。本端点是
  **单主机显式请求**，不存在集合完整性问题；强行套用会造出一个无意义的恒 0 计数。

## Verification

- `python -m pytest backend/tests/api/test_agent_log_query.py -q` → **5 passed**（原 3 + 新 2）；
- **红绿双向**：临时移除审计调用 → `test_retired_host_log_tail_is_allowed_and_audited`
  **失败**；还原 → 5 passed；
- **放行集未变**（本单核心性质）：退役机尾读返回 **200**（两者皆断言）——证明是
  「允许 + 审计」而非误改成拒绝；
- **负向用例**：活跃主机**不落**该审计（避免把正常取证计入退役审计面）；
- **回归**：`test_agent_log_query` + `test_logs_query_pagination`
  + `test_host_retirement_api_1801` + `test_ssh_security` → **38 passed**；
- `ruff check` 两文件 → All checks passed；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿。

## Revisit

- **「仅显式 admin 触发」在当前鉴权形态下无法表达**：该端点用 agent secret 认证、
  无用户上下文，故 ADR 措辞的 admin 限定**未被满足**，本单只落地了「审计」这一半。
  若要真正满足，需为该端点引入用户会话/admin 断言——**那是鉴权契约变更**，应独立
  裁决（并需同时决定前端调用路径如何携带身份）。本单不自行扩面。
- **审计的消费面**：当前只写入 `audit_logs`，无人读。若需要「退役机取证留痕」的
  可见面（如列表/告警），属观测面扩展，需独立裁决。
- **矩阵 row 12 的其余面**：该行还含 `agent_api.py:2887-2910`（recovery/sync 覆写
  boot_id）、`heartbeat.py:403`（设备 re-home）、`routes/scripts.py:212,231,241`
  （脚本目录重拉）——**未**在本单覆盖，归 #1805 后续切片。
- **#1805 剩余待领项**：182d4e-F7 十场景全量 mutation 仍未覆盖。
