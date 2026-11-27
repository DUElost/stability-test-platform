# B11-U1：Monkey AEE 提单表单 A 段只读生成引擎与 API（#4199 v1.1 / #4174）

Status: implemented
Class: feature

关联：[#4199](https://github.com/DUElost/stability-test-platform/issues/4199)（批次 B11 方案 v1.1）、
[#4174](https://github.com/DUElost/stability-test-platform/issues/4174)（v1.1）、
[#4054](https://github.com/DUElost/stability-test-platform/issues/4054)。

## Decision

按批次 **B11（#4199 规划方案 v1.1）** 与 **#4174 v1.1** Owner 裁决，落地阶段 1（B11-U1）后端 A 段只读提单表单生成引擎与 REST API，并闭合第 1 轮独立复核退回（PR #4204 评论 `#7211826188`）全部项：

1. **硬覆盖率门禁 + 快照双冻结 + 严格发生身份绑定**（`backend/services/jira_handoff_identity.py`）：
   - 覆盖率门禁为硬阻断（移除 `allow_incomplete_coverage` 旁路），双向核验期望主机集与有效报表/合法空回执集合；坏表、错表头（含空表）、缺必需列、非期望 host、未关闭轮次及越根/软链输入均不得计为有效覆盖；
   - `snapshot_cutoff` 同时冻结 DLE 观测时间与报表分片选择（`CURRENT` `created_at <= cutoff`、`DEDUP_FIRST` 已关闭 `FINAL` + `SUPPLEMENT` 且 `closed_at <= cutoff` 分片并集）；
   - 按分片归属（`plan_run_id` + `host_id` + `serial`）与 `occurrence_key` 一致性核验 DLE；缺 DLE、key 冲突、跨 run/host 错绑与有伴随缺 key 均入 `unbound_rows`；`compat_serial_entry` 严格收窄到「无伴随映射的历史 `CURRENT` 分片且唯一锚定」（多候选歧义入 `unbound_rows`，`DEDUP_FIRST` 缺伴随不回退）；
   - `*_temp` / `CurProcessNone` 与未绑定行全部入 `unbound_rows`，全未绑定（0 有效事件）时预检与 422 均返回完整 `unbound_rows` 明细；
   - build 严格按源 run 的 `device_builds` 归属（同串号换 build 续跑不错归）；本次/基线划分抽为纯函数 `classify_event_baseline`（2010 类坏钟回退 `DLE.detected_at` 供 `#4033` 复用）。
2. **发布工具只读隔离 + 强输出契约校验 + 冻结存储**（`backend/services/jira_handoff_builder.py`）：
   - 校验发布目录 `release.json` 身份（`version >= 2.1.1` 且含 `git_commit`）；每次调用 `-dedup_org` 将脚本/模块/`config/` 轻量树复制到隔离临时目录运行（仅只读引用原 `venv`，不向原目录写锁/日志/配置、不软链 `config/*`），彻底隔离上游 `start-log-scan 2.1.1` `BaseScan.py:2006` 写空 `config/blacklist.txt` 缺陷；
   - 对 `*_members.json` 与配对终表 `.xls` 执行与 `scan_membership.py:72-109` 同等强校验（`schema` / `schema_version` / 输入 `_org.xls` SHA256 / 逐行 `path` / `branch` / 分组守恒 / 终表完整性），任何不符或损坏终表均 fail-closed 转结构化 `JiraHandoffError(502)`；
   - 不同 build 附加确定性短哈希防 slug 碰撞覆写；保留配置中心根为信任锚，统一校验受控根及其祖先无 symlink、下载目标为普通可读文件；严禁调用 `-m` / `-m 0` / `aee_extract`、严禁写 15.4 共享根；
   - 冻结产物原子写入 `_meta/handoff/<handoff_id>/`（含 `manifest.json` + `manifest_sha256`、15 列预览表与 5 页复核工作簿，B3 公式注入中和），零 DB 迁移，经 `record_audit` 留痕。
3. **API 与字面平台契约**（`backend/api/routes/jira_handoff.py`、`backend/api/schemas/jira_handoff.py`、`backend/main.py`）：
   - 提供 `preflight` / `bundles` / `bundles/{id}` / `bundles/{id}/files/{kind}` 五个受登录保护端点；
   - `platform` 仅接受字面 `"mtk"`，非字面 `"mtk"`（含 `"unisoc"`、`"MTK "`）一律返回 HTTP `400`；不触碰 `frontend/` 与 `backend/agent/`。

## Alternatives

- **方案甲（在原发布工具目录加文件锁串行执行 `-dedup_org`）**：否决。第 1 轮复核实证 `start-log-scan 2.1.1` 在离线 `-dedup_org` 下会打开并截断 `config/blacklist.txt`，且锁文件本身也会写入原发布目录 `tmp/`；改为每请求隔离轻量副本可彻底保证原发布目录零写入。
- **方案乙（向 PG `issue_occurrence` 扩写 9 列厂商文本或保留 `allow_incomplete_coverage` 旁路）**：否决。分别违反 #4174 Owner 裁决（一期零迁移）与 #4199 v1.1 覆盖率硬门禁要求。

## Verification

实际运行命令与结果见 PR #4204 正文「验证结果」节（涵盖 #4199 v1.1 §4.2 全部 13 类反例、API 端点契约、审计守卫、Excel 依赖台账、`check:quick` 与 `check-internal-ip-leak.py`）。

## Revisit

- 上游 `stability_Start-Log-Scan` 独立发版修复 `BaseScan.py:2006` 条件反转（#4199 §8 登记，不阻塞 B11）；
- B11-U2 合入后在独立测试服务器联调前端 `/issue-tracker` 首 Tab「提单表单生成」与 `PlanRunDetailPage` 复数深链；
- B11-U3 消费本单元冻结 `manifest.json`（校验 `manifest_sha256`）完成 15.4 受控导出与终稿 `.xls` 发布；
- UNISOC 提单表单待 ADR-0062 v2.0 阶段 C 切量后再放开 `platform` 白名单。
