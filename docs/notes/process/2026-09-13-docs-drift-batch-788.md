# Agent Note: 文档/CI 失配批逐项复核与收口（#788）

Status: implemented
Class: process
Issue: #788

## Decision

按「**先验证每条声明、再动手**」处理 #788 的 9 项。结果：**5 项已在别处修好（声明陈旧）**、
**4 项为真缺口**。本轮只修真缺口，并在 issue 留逐项判定供 owner 关单。

| 项 | issue 声明 | 实测（2026-09-13） | 处置 |
|---|---|---|---|
| 1 | AGENTS.md 仍写「回退 `-merge_files` + 30000 字符上限」 | `grep -n "merge_files\|30000" AGENTS.md` = **0 命中** | 陈旧，不动 |
| 2 | `2026-adr-0025-log-flow-sequence.md` 仍把 `CONTINUOUS=1` 当现网 | 5 处仍在（L10/145/151/261/279），而 `event_uploader.py` 注有「#287：CONTINUOUS 全量模型已删除」 | **修**（5 处改为「已删除」+ 日期更新标记） |
| 3 | PR 模板称 security concerns 会阻断合入 | 实际已写「**异步顾问…不阻断合入**」 | 陈旧，不动 |
| 4 | `pr-update-branch.yml` 仍列旧名「PR Agent (DeepSeek)」 | 实际已是 `"PR Agent (advisory review)"` | 陈旧，不动 |
| 5 | 3 条 `../adr/`、`../design/` 断链 | 逐链解析：**3/3 断**，且无其他同型断链 | **修**（前缀改 `../../`） |
| 6 | CLAUDE.md migration id 错字 `q2r3s4t5u6v7w8` | 该串**已不在** CLAUDE.md | 陈旧，不动 |
| 7 | CLAUDE.md:17 / `.cursor/rules` 的 `main.py` 锚点漂移 | 两处**均已无** `main.py` 引用 | 陈旧，不动 |
| 8 | `01-execution-pipeline.md` §8.3 env 表未注 #295 角色键分离 | **真**（表内无 #295/角色键痕迹） | **修**（补角色分离表 + 权威文档指针） |
| 9 | #728 缺 Agent Note | `docs/notes` 下无 gpu 相关 note | **修**（事后补记，见 `2026-09-01-gpu-check-v104-monitor-judgement.md`） |

## 一处**必须偏离 issue 原文**的地方

第 8 项原文写「旧键仅兼容回落 + WARNING」。**该描述在动手时已不成立**：
`scan_tool_legacy_env_fallback` 在代码中已不存在，`docs/development/environment-variables.md:49`
已写明「**#518 起不再回落旧无前缀键**」。若照抄 issue 原文，等于往文档里写一条**新的假事实**。
故本轮按**当前代码真相**写：控制面只认 `STP_BACKEND_DEDUP_SCAN_*`，无回落。

## Alternatives

- **一次性把 9 项都改**：不选。1/3/4/6/7 已被别处修好，改它们只会制造无意义 diff，
  并让 reviewer 去核对一个不存在的问题。
- **直接关掉 #788**：不选。仍存 4 项真缺口，其中 2 处属「指引实现者扑空」形态
  （读者按文档去找一个已删除的逃生阀 / 打开 404 链接）。
- **顺手删掉 §8.3 里无控制面读取点的两行**（`STP_JIRA_TOOL_*`）：不选。无法证明
  它们不被**仓库外**工具消费（部署 env 示例与归档设计中仍有出现），故只加核查标注、不删行。

## Verification

- **断链**：对 3 个文件跑「解析全部相对链接 → `os.path.exists`」脚本，**修前 3 断、修后 0 断**；
  并单独确认修正后的 3 个目标文件存在（含 `ADR-0031-**A**-appendix-…` 的真名——
  该文件名与链接显示文本不一致，只改前缀不改文件名）。
- **第 8 项**：逐键检索代码读取点 —— `STP_BACKEND_DEDUP_SCAN_*` → `services/dedup_scan.py`；
  无前缀键 → `agent/scan_runner.py`；`STP_JIRA_TOOL_*` 与 `STP_DEDUP_AUTO_SCAN` → 控制面**无**读取点。
- **门禁**：docs-only 变更，`check:quick` 结果见 PR 描述。
- **未验证（诚实标注）**：这些文档表述对读者的实际影响（读者含人与实现 Agent），
  本轮只做事实更正，未做可读性/结构改动。

## Revisit

- 本轮暴露**多条目批次单的通病**：审计时点为真的条目，10 天后可能已被别处修掉。
  建议此类批次单在 owner 关单前做一次逐项复核（本轮即为此做的）。
- **第 5 项的根因（工具覆盖面）本轮未动**：`tools/dev/check_governance_surface.py` 的
  `check_links`（S2）只覆盖权威面文件（CLAUDE.md / AGENTS.md / ADR README /
  Execution Contract / hub 索引等），**不扫 `docs/notes/**`**——这正是「08-26 抓到过又复发」
  的原因。扩展扫描面属**扩大阻塞门禁**的决策（会让他人 note 的断链变成红灯），
  且「工具覆盖面」主题已有在窗认领（#1569 required checks / #1585 memory-lint），
  故本轮只修数据、不扩门禁；建议由 owner 决定是否纳入扫描面。
- `STP_JIRA_TOOL_*` / `STP_DEDUP_AUTO_SCAN` 是否存在**仓库外**消费者，需 owner 裁定后
  决定「删行」还是「补文档说明消费者」。
