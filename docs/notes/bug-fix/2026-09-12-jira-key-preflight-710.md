# JIRA project key 提单前存在性探测（#710）

Status: implemented
Class: bug-fix

## Decision

ADR-0029 v2.5 D12 遗留的第二件事（提单前探测）落地，best-effort 不阻断：

- `backend/services/jira_project_key.py` 新增 `probe_jira_project_key(key)`：
  配置了 `STP_JIRA_BASE_URL`（可选 `STP_JIRA_TOKEN` Bearer）时请求
  `GET {base}/rest/api/2/project/{key}`；200→True、404→False、其它/异常/未配置→
  None（未知，静默/警告但不阻断）。
- `backend/api/routes/dedup.py` 的 `source=plan_run` 提单路径：解析出 key 后调用
  探测；`False`（明确不存在）记 WARNING 提示登记簿更正，**不阻断提单**——vendor
  工具仍按其默认映射回落，与既有 best-effort 语义一致。
- 新增可选环境变量 `STP_JIRA_BASE_URL` / `STP_JIRA_TOKEN`，登记
  `docs/development/environment-variables.md`。

保持既有边界：格式宽松校验（≤32、无空白）与详情页「未验证」标记不变；本探测只
补「存在性」这一维度，且无凭据时不产生任何行为变化。

影响面：`backend/services/jira_project_key.py`、`backend/api/routes/dedup.py`、
`docs/development/environment-variables.md`、
`backend/tests/services/test_jira_project_key_probe_710.py`。

## Alternatives

- 在详情页语义校验里做存在性探测——校验是同步保存路径，网络探测会拖慢/耦合；
  提单前一次性探测更贴合「登记时格式、使用时存在」的分工。
- 探测失败阻断提单——违反现有 best-effort 语义（映射缺失只降级不阻断），且
  JIRA 短暂不可达会阻断正常提单；按 issue 要求仅 WARNING。
- 新增 JIRA 专用配置模型/DB 行——本次只需两个环境变量即可支撑，避免过度设计。

## Verification

- `backend/tests/services/test_jira_project_key_probe_710.py`：无配置→None；
  200→True（含 URL/Bearer 断言）；404→False；500/异常→None。6 passed。
- `backend/tests/api/test_dedup_jira_endpoints.py` → 32 passed（集成路径未破坏）。
- `ruff check` 变更文件通过。

## Revisit

- 若运营要求「探测成功回填已验证状态」（issue 的可选项），需在 `test_project`
  增加验证态字段与管理入口，另开。
- JIRA REST 版本差异（Cloud vs Server）可能需 `/rest/api/3` 或鉴权方式调整；
  当前按 Server v2 + Bearer，遇 Cloud 再扩展。
