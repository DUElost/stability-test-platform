# R02 台账（#910）收口：7/7 回源码复核补齐与登记面关闭

Status: implemented
Class: process

## 背景与原始事件

R02「认证、授权与安全边界」审查台账 [#910] 于 2026-09-07 建立（基线 `963bdd18`，
7 确定缺陷 + 4 设计/既有风险），是 R01–R15 中唯一未收口的台账。收口主题的原始
事件链：09-14 只读审计条完成 F01–F05 回源码复核并**明示 F06/F07 未展开**；
09-16 ADR-0037 联审项闭环后，「台账收口」成为本台账唯一剩余事项。本 note 对应
2026-09-16 的收口执行（复核基线 `6b21e3b2`）。

## Decision

1. **收口口径沿用 #891/#945 先例**：「全部子单 CLOSED 或已登记去向，且无在飞
   Execution 引用本台账」→ 发布收口对照评论并关闭台账 issue；台账关闭只表示
   **登记面收口**，不改变 `PROJECT_REVIEW_PLAN.md` 四态口径下 R02 区域的
   「待验证」状态（该区从未做过动态验证）。
2. **补齐 09-14 审计的抽验缺口**：F06（#905）/F07（#907）逐条回源码复核，
   使 7/7 确定缺陷全部达到「回源码确认实现形态与缺陷描述一致」标准，并对
   F01–F05 锚点在 `6b21e3b2` 做漂移抽查。
3. **风险项按去向登记而非滞留台账**：#906（OPEN，ADR-0035 Accepted v1.2，
   实施单另行拆分，挂 Epic #720/#46）与 #91（OPEN，自身即追踪载体）留开；
   #908/#909 已 CLOSED 并复核关单形态（#1404 换钥显式确认 + 指纹审计；
   #1002 ADR-0024 v1.1 例外契约化，复议触发 #46）。
4. **文档同步随 PR 走队列**：`PROJECT_REVIEW_PLAN.md` §5/§5.1 的 R02 行由
   「进行中（台账未收口）」改回与其余 14 区一致的「待验证」口径；台账 issue
   的关闭与 PR 合入解耦（先例：#891 09-13 关闭、总纲 09-14 才核对更新）。

## Alternatives

- **保持 #910 OPEN 直到 #906/#91 解决**：否决。两项各自挂独立 issue，状态事实源
  已在子单上（09-14 审计的结论「逐项挂 issue 号，issue 状态即事实源，故不漂移」）；
  台账继续开着反而制造第二事实源与漂移面。
- **不补 F06/F07 直接收口**：否决。09-14 条明文「若要 7/7 全部逐行复核，需再开一轮
  ——本条不宣称已完成」；跳过即把审计自己标记的缺口固化为假闭环风险。
- **等本 PR 合入后再关台账**：否决。收口证据不依赖文档合入（证据在子单/源码/
  已关 PR 上），且队列合入时点不可控；沿用 #891/#945 的「先关单、后对账文档」
  顺序，评论中显式标注 PR 在队。

## Verification

只读复核（未跑测试；本 note 随附 diff 为纯文档）：

> 本小节为**第一轮（静态回源码）**；同日第二轮动态验证证据见下文
> 「动态验证（第二轮，2026-09-16）」，两轮合计构成 R02 升「已完成」的登记。
- **F06 #905**（关单 PR #1023）：`backend/services/precheck/sync.py:40-53`
  `nfs_path_to_local` 三道校验俱在——绝对切片拒绝（`rel.is_absolute()`）、
  `..` 组件拒绝、`resolve()` 后 root 包含性（拦 root 内 symlink 外指）；
  全仓唯一消费点 `sync.py:184`（`push_mismatched_scripts`），无旁路。
- **F07 #907**（关单 PR #1034）：三个动作面在当前 HEAD 均有可归责审计
  （`record_audit` 带 username/user_id/request，成功与失败路径都落）——
  死信重放 `backend/api/routes/hosts.py:1223-1282`（`dead_letter_replay`）、
  JIRA 取消 `backend/api/routes/dedup.py`（`jira_run_cancel`，404 探测路径同样
  落审计）、Agent 配置重载 `dedup.py:548+`（`agent_config_reload`，失败只记
  异常类型不记参数原文）。
- **F01–F05 锚点漂移抽查**（09-14 已逐条复核，本次确认 `6b21e3b2` 未漂移）：
  `auth.py:153`（sub=不可变 PK）、`users.py:170,268,315`（token_version 三处
  自增）、`auth_session.py:35`（唯一校验点）、`metrics.py:30,131`（收敛
  `authenticate_token`）、`socketio_server.py`（Origin 服务端白名单校验，
  非仅 CORS 响应头）。
- **风险项**：`ssh_security.py:175-258`（#908 换钥默认拒绝 + 指纹审计，含 #1655
  删集错域加固）；`ADR-0024` v1.1 节（#909 例外边界 + SameSite/CSRF 测试固化 +
  #46 复议触发）；`ADR-0035` 头部（#906「不随本 ADR 关闭，实施单另行拆分」）。
- Registry 前检：declare `--issue 910` 未命中在窗查重（即收口前无人认领）。
- 收口对照表发布在 #910 收口评论；本仓库文档同步 PR 见评论内链接。

### 动态验证（第二轮，2026-09-16）

§5.1 转态条件「关键结论取得运行证据（隔离环境动态复现）」的执行记录：

- **基线**：`a0e6a4c8`（收口主干 `6b21e3b2` 之后的最新 main）；
- **隔离方式**：`TEST_DATABASE_URL` 确认未设 → conftest 拉起独立
  testcontainers `postgres:16`，未触生产库；跑后巡检
  `check_test_containers.py` 零残留（本进程容器由 sessionfinish 正常回收）；
- **命令**：`TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest -q` +
  8 个归属文件（`test_auth_cookie_session.py` 15、`test_metrics_auth.py` 8、
  `test_dashboard_auth.py` 14、`test_cors_hardening.py` 3、
  `test_precheck_sync.py` 15、`test_dedup_jira_endpoints.py` 34、
  `test_log_signal_dead_letter_api.py` 8、`test_ssh_security.py` 24）；
- **结果**：**121 passed / 0 failed（29.95s）**；
- **F 项 ↔ 运行断言对应**：F01
  `test_recreated_same_username_cannot_honor_old_token` 等 3 项；F02
  `test_refresh_rotation_rejects_replayed_refresh_token`；F03
  `test_change_password_invalidates_existing_token` /
  `test_admin_toggle_active_invalidates_existing_token`；F04
  `test_metrics_rejects_token_of_disabled_user` /
  `test_dashboard_rejects_token_of_disabled_user`；F05
  `test_dashboard_rejects_foreign_origin_with_valid_token` + cors 硬化套件；
  F06 `test_nfs_path_to_local_rejects_{parent_traversal,absolute_remainder,symlink_escape}`
  （正是 #905 三道逃逸面）；F07 `test_reload_success_audited` /
  `test_reload_emit_failure_audited_and_reraised` / 死信重放审计断言（直读
  `AuditLog` 行）；另含 #908 换钥守卫套件（24 项，风险项超额证据）。
- **边界**：运行证据 = 隔离环境回归断言，不是生产流量复现；§4.1「已完成」
  语义不变。

## Revisit

- ~~R02 区域状态从「待验证」升「已完成」仍需 §4.1/§5.1 约定的动态证据~~
  **已满足（2026-09-16 第二轮，见上）**；后续若认证/会话核心面发生结构性改动，
  按 §4.1 进入下一轮审查重议，不在本 note 范围内。
- 未覆盖面如实登记：故障注入 / 多副本 / 真机链路不在本次动态验证范围
  （R02 关键结论不依赖这些载体）；#91（多副本限额）本就直接以「未验证」立论。
- #906 实施单拆出、ADR-0035 §6.1 任一触发条件命中，或 #91 多副本部署形态变化时，
  在**各自 issue** 推进，不重开本台账；若需引用 R02 历史，用 #910 收口评论。
- #46（internal TLS）落地时按 ADR-0024 v1.1 复议触发器收窄 Secure 豁免——该动作
  归 #46/ADR-0024，与本台账无关。

[#910]: https://github.com/DUElost/stability-test-platform/issues/910
