# 文档真值对拍第二批：专项 runbook / ADR-0015 / 03-frontend / MTK ttyACM / pg-guard 头

Status: implemented
Class: bug-fix

## Decision

2026-09-21 只读审计（7d 文档漂移轮）登记的「文档与实现不符」中，取不与他人在飞 PR 撞文件的五处一并收口；同一批里被在飞 PR 覆盖的四处（#2989 ADR 索引 / #2992 ADR-0048 轴引用 / #3001 05-data-model / #3003 hub 头部与 DOC-MAP）**本 PR 不动**，留待那些 PR 合入后另行收口。

逐处改动与判据：

1. **#2995 专项 runbook 的端点 URL**——`docs/operations/new-specialty-onboarding-runbook.md` 写 `GET /api/v1/plans/specialties`，实际路由是 `GET /api/v1/specialties`（`backend/api/routes/plans.py:45` 的 `prefix="/api/v1"` + `:558` 的 `@router.get("/specialties")`）。旧写法落到 `GET /plans/{plan_id}` 上被当 plan_id 解析，返回 **422** 而非干净的 404。改 URL 并写明「端点不在 `/plans` 下」与响应形状 `data: [{key, display_name, …}]`。
2. **#2996 ADR-0015 的 audit_logs 模型**——`resource_id` 原写 `Integer`，实为 `String(64)`（`backend/models/audit.py:33`；宿主资源为 UUID 时是文本）。索引段只列 `ix_audit_user_ts` / `ix_audit_resource`，漏了 `#2694` 补入的 `ix_audit_action_ts`（action + timestamp）与 `ix_audit_ts`（timestamp 单列），而这两条正是 facets 在 26 万行表上免全表聚合/全表排序的前提。原地更正并在正文标注来源，与仓内 ADR 纠错先例（#2950/#2929 等）同形。
3. **#3000 03-frontend 的路由表与组件表**——删掉不存在的 `/resources`（`frontend/src/router/index.tsx` 全仓无此路由；WiFi 资源池是 admin-only 的 `/wifi`，已在同一表的管理行点明），补 `/projects`、`/projects/:projectKey`、`/test-suites`、`/test-suites/:suiteId`、`/settings/ai-assistant`；`WatcherSummaryCard` 已在 `a58e45e3` 作为无引用死组件删除，异常聚合现由 `AnomalyDashboard` 承担（`PlanRunDetailPage.tsx` 把 watcher 查询的 data 交给它渲染），故删该行并把职责并入 `AnomalyDashboard` 行；`PipelineEditor` 已无实现，改为实际的 `pages/orchestration/PlanEditPage`（画布/检查器在 `components/pipeline/`）。测试文件数**删除硬编码**（原写 77，实测 87 `*.test.tsx` + 42 `*.test.ts` = 129）——这类数字必随改动漂移（同 #2663 的计数陈旧族），改为指向 `development/testing.md` 的口径。
4. **#3002 MTK ttyACM 权限形态**——`docs/design/2026-08-honor-flash-firmware-routing.md` 仍写 `MODE="0666"`，而安装链默认写 `GROUP="dialout", MODE="0660"`（`backend/agent/install_agent.sh:271-284`，0066 仅在 Agent 用户未持久属 dialout 时退化并告警），`flash_preflight v1.0.4` 把 0666 当 legacy 形态。除段落外同步 `§3` 的 `strict_env_check` 行（原文「dialout 组或 udev 0666 规则」同样把 0666 当正典）。
5. **#3004 `stp-pg-guard.timer` 头自述**——该单元不在站点安装清单（`tools/site_config/stages.py` 的 `MONITORING_SAMPLER` 无它），`.service` 已写明这一点，只有 `.timer` 自称「Rendered by the site installer」。而该标记正是本仓判定「站点安装资产」的判据（`stages.py:88` `DISTRO_DEFAULT_MARKER`，共享路径归属守卫以此为锚），误标会误导按它手工安装控制面宿主的人。改为与 `.service` 同款头。

影响面：纯文档与注释，无行为语义变化（`declare --test-impact none`）；唯一非文档文件是 `.timer` 的注释头，systemd 忽略注释，`Description`/`[Timer]` 段未动。

## Alternatives

- **ttyACM 只改被点名的段落**：放弃。同文件 `§3` 表行的「dialout 组或 udev 0666 规则」是同一错误前提的另一处表述，只改一处会留下自相矛盾的文档。
- **03-frontend 保留计数、把 77 更新为 129**：放弃。计数与 `*.test.tsx / *.test.ts` 的分类会持续漂移（本仓已有 #2663 一族的先例），改成指向 `testing.md` 的口径说明，把「数字」这件事交给实测。
- **ADR-0015 追加「勘误」小节而不动正文**：放弃。ADR 表内联类型是读者直接照抄的对象，勘误块留在末尾会继续被漏读；仓内对 ADR 事实性错误的惯例是原地更正（#2922/#2929/#2950）。
- **把被在飞 PR 覆盖的四处（#2989/#2992/#3001/#3003）也一并改**：放弃。`docs/adr/README.md`、`docs/DOC-MAP.md` 已被 draft PR #3005（ADR-0033 Phase A）触及，`docs/design/05-data-model.md` 被 PR #3012 声明覆盖，`#2992` 的判据还依赖 ADR-0048 v1.1（PR #3012）的最终裁决语义——同文件并发编辑会把冲突推给对方。

## Verification

- **端点真值**：`backend/api/routes/plans.py:45`（`prefix="/api/v1"`）+ `:558`（`@router.get("/specialties")`）；全仓无 `plans/specialties` 路由。
- **模型真值**：`backend/models/audit.py:33` `resource_id = Column(String(64))`；`:22`/`:25` 两条 #2694 索引。
- **路由真值**：`frontend/src/router/index.tsx:132-162`（无 `resources`；`test-suites`、`projects`、`settings/ai-assistant` 在册）；`WatcherSummaryCard` 删除见 `a58e45e3`；`PipelineEditor` 全仓零命中。
- **计数**：`find frontend/src -name '*.test.tsx' | wc -l` = 87、`*.test.ts` = 42（改后正文不再含该数字）。
- **权限形态**：`backend/agent/install_agent.sh:271-284` 双形态与退化条件；`backend/agent/scripts/flash_preflight/v1.0.4/flash_preflight.py:81-89`（`_UDEV_RULE_LINE` / `_UDEV_RULE_LINE_LEGACY`）与 `:188-196`（`_udev_rule_form` 判据）。
- **标记语义**：`tools/site_config/stages.py:88` `DISTRO_DEFAULT_MARKER`、`:436` 的 fail-closed 判据、`MONITORING_SAMPLER` 清单内无 `stp-pg-guard`；`tests/test_site_install.py:888/1330` 的断言只作用于安装器渲染产物，与本次删标记不冲突。
- 门禁与测试：见 PR 描述（`check:quick` 与 `tests/test_site_install.py`、`backend/tests/test_deployment_files.py` 实跑结果）。

## Revisit

- 专项管理面若立项（runbook §3 记的「已知缺口：specialty 无 REST 管理路由」），回来把该步从「DB 登记 + 联系维护者」改写为管理面操作。
- ttyACM 的 0666 退化形态若随安装链收敛而消失（#2353 的双形态并存期结束），本文件里的 legacy 说明应删除，而不是长期并陈。
- 被在飞 PR 挡住的四处（#2989/#2992/#3001/#3003）在那些 PR 合入后仍需一轮收口，别把本 PR 当成整批完成。
