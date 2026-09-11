# 有效审查文档集审计（元审查）

> **状态**：供综合轮与用户参考的只读审计；不构成裁决本身，也不构成全面审查交付完成声明。
> **审计时点**：2026-09-11（观测：`origin/main` = `2e67a131`；PR #1349 已合入、#1369 在队列）
> **方法**：代码级抽验 + 计数重算 + 联合全集引用扫描 + Registry/门禁核验；**未**运行 pytest/Vitest/迁移。
> **边界**：所有计数为时点值且各稿口径不同（见 §2）；引用其数字必须带口径与时间。本审计不修改被审文档。

---

## 0. 审计对象（9 份）

| # | 文档 | 作者 / 会话 | 状态 |
|---|---|---|---|
| 1 | `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fdca41.md`（`CA-*`） | CodeBuddy `fdca41` | 主干 |
| 2 | `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_b77c27-verification.md`（`IV-*`） | zcode `b77c27` | 主干 |
| 3 | `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_b77c27-confirmation.md`（`CF-*`） | zcode `b77c27` | 主干 |
| 4 | `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_4e188e.md` | Cursor `4e188e` | 主干（#1349 入库） |
| 5 | `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_e16d6d.md` | Cursor `e16d6d` | 主干（#1349 入库） |
| 6 | `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_pf8rII-convergence.md` | opencode `pf8rII` | 主干（#1349 入库；**裁决层**） |
| 7 | `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_85d793.md` | Codex `85d793` | 队列 #1369 |
| 8 | `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_705379-synthesis.md` | Cursor `705379`（恢复稿） | 队列 #1369 |
| 9 | `REVIEW_THREE_QUESTION_CONFIRMATION_2026-09-11_7d4f85.md` | **dsh** `7d4f85` | 在途（未入库、未登记） |

配套 Note 共 8 份（不逐一列出）。

## 1. 文档级抽验（代码级，2026-09-11）

| 抽验项 | 原断言 | 核验结果 |
|---|---|---|
| CF：redis 客户端无超时 | `aioredis.from_url` 无 socket 超时 | ✅ `backend/main.py:147-151` 仅 `encoding` / `decode_responses` |
| CF：锁表无淘汰 | `_locks` 只增不删 | ✅ `backend/realtime/log_writer.py:20-26` 无 pop/clear |
| CF：脚本时钟混用 | 脚本大量使用 `time.time()` | ✅ `grep -rl` = 74 个文件 |
| 85d793-F01 | #901 refresh 原子消费未前提化（`revoke()` 返回值被忽略） | ✅ 结构成立：轮转调用点未消费返回值（`backend/api/routes/auth.py` 轮转段） |
| 85d793-F02 | #1123 取消路径互斥提前释放 | ✅ `backend/tasks/saq_tasks.py:123-125` `finally: _SYNC_OVERLAP_GUARDS.end(key)` |
| 85d793-F03 | #942 契约漂移（design 仍 Proposed） | ✅ `docs/design/2026-09-08-seed-migration-governance.md:3` |
| 85d793-F04 | 通知恢复查找缺 `run_id` 即返回空 | ✅ `backend/services/notification_service.py:238-249` |
| IV：验证网连续两晚失败 | backstop 09-08/09-09 = failure | ✅ 属实；**更新：09-10 = success（已恢复）** |
| IV-X01（对恢复稿更正） | §7 映射缺 R02/R03/R04/R06–R10；§3.1 计数失实（R02=2、R14=14、合计 69） | ✅ 更正成立；恢复稿相应段落**仅可留档** |
| IV-X02（对 CA 稿更正） | CA-C03 措辞过强、CA-Q04 条件项不成立、§5 登记不全 | ✅ 三点均成立（汇聚稿已裁定 CA-C03 证伪） |

## 2. 跨文档一致性与裁决链

- **裁决链已闭合**：`pf8rII-convergence` 对 Q1/Q2 分歧的裁定与 `IV`/`CF` 一致；`CA` 的 Q1 否定论
  （CA-C03「无整块被遗忘业务域」）被证伪、Q2「可持续=条件成立」被更正为「条件未满足」。
  **有效结论以主干裁决层为准。**
- **backstop 状态更新**：由「连续两晚红」更新为「09-08/09-09 红 → 09-10 绿」；
  「验证网为红且不阻断」的表述应带时点。
- **计数漂移与口径**（全部为 2026-09-11 时点值，**禁止跨口径直接比较**）：

| 口径 | 数值 | 出现于 |
|---|---|---|
| OPEN 总数 | 205（本审计）；209（`7d4f85`）；243→237（`CF`） | 各稿 |
| R 相关开放 | 32（标题含 `R\d+[-–]`）；36（`Rxx-Fyy|Ryy` 严格匹配） | 本审计 vs `7d4f85` |
| pre-R 存量开放 | 152（`createdAt<2026-09-07`）；148（`#<880`）；150（汇聚稿） | 本审计 vs `7d4f85` vs 汇聚 |
| 存量池初值 | 44（`IV` 早期观测，后被自身口径修订方向确认低估） | `IV` |

- **一致项**：`#1246` 为五稿一致最高优先；PR 路径全量测试恒 SKIPPED（`ci.yml:22-25,81-83`）；
  跨区收口未启动（总纲 §6）。

## 3. 引用完整性（联合全集扫描）

- **零断链**：`fdca41`、`b77c27-verification`、`b77c27-confirmation`、`4e188e`、`85d793`、
  `705379-synthesis`、`7d4f85`。
- **待修**：
  1. `pf8rII-convergence` → `./PROJECT_REVIEW_R01_R15_SYNTHESIS_2026-09-11.md` 断链
     （该稿已恢复为 `..._705379-synthesis.md`，链接名需更新）；
  2. `e16d6d` → 3 份退役稿（`5e3831` / `705379` / `f61411`）断链（退役去向已登记于 promotion Note）。
- **已闭合**：`e16d6d` 对 `85d793` 的引用随 #1369 合入生效。

## 4. 过程合规

- **命名**：8/9 合规（§4.1：会话后六位 + 可选角色后缀）；`7d4f85` 采用
  `REVIEW_THREE_QUESTION_CONFIRMATION_*`——其 Note 自述为「用户指定命名」，与后落地的 §4.1 冲突，
  需裁决（改名并入规范，或保留并登记例外）。
- **登记**：`4e188e` 有 Registry 记录（FINISHED）；`e16d6d`、`pf8rII`、`7d4f85` 无 declare 记录（声明缺口）。
- **索引**：本 PR 补齐 #1349 三稿的 DOC-MAP 登记；`85d793`/`705379-synthesis` 由 #1369 登记。

## 5. 结论可信度分级

- **A 级（多源一致 + 代码实证，可直接行动）**：`#1246`；PR 路径全量测试 SKIPPED；
  redis 无超时；`_locks` 无淘汰；85d793 F01–F04；测试库隔离（#1300）；ADR-0031 附录未登记；
  跨区收口未启动；「同类第 2 次 ⇒ 机制级修复」组合治理口径。
- **B 级（单源/时点数据，引用需带口径+时点）**：各稿 issue/PR 计数与关单率；pre-R 存量池规模（148–152）。
- **C 级（历史/已更正，仅留档）**：恢复稿（`705379-synthesis`）全部计数（IV-X01 已列两处失实）；
  CA-C03 原措辞；退役未入库稿（`5e3831`/`705379`/`f9a21b0f`×2/`f61411`）。

## 6. 处置建议（带推荐）

| # | 事项 | 推荐 |
|---|---|---|
| 1 | `pf8rII` 断链更新为恢复稿名 | 下一次文档维护顺手修，或综合轮统一处理 |
| 2 | `7d4f85` 命名与登记 | 按 §4.1 改名 + declare + 入库；若弃用则书面备案 |
| 3 | 85d793 F01/F02 反例 | 优先转回归测试；或先开 F02 残余单，再开 F01 |
| 4 | 计数口径归一 | 综合轮附「口径+时点」表；引用禁止跨口径比较 |
| 5 | 收敛冻结 | 以 `pf8rII` 为唯一裁决基准；新稿默认只做增补 |
| 6 | `/tmp` 备份（退役 5 稿） | 迁出到稳定位置，或书面弃用备案 |

## 7. 更正记录（本审计自身）

- 本审计初版（会话内）曾将 `7d4f85` 归属为 opencode；**更正为 dsh**（证据：
  `~/.dsh/storages/session_projcache/sessions/session-f4f2a372-….json` 会话本体；其 Note 自述
  `DSH_SESSION_ID`）。opencode 库中的命中系汇聚会话（`ses_f7f07f53…`）对它的引用。
- 规则固化：**会话号在他库命中 ≠ 归属**；归属必须看该 harness 会话存储中的记录本体；
  检索须覆盖全部已知 harness 目录（codex/cursor/codebuddy/zcode/claude/dsh/opencode）。

## 8. 复现（只读）

```bash
# 计数（注意口径：标题正则 vs issue 号段；createdAt vs 时点）
gh issue list --state all --limit 1000 --json number,title,state,createdAt
# 验证网状态
gh run list --workflow=main-ci-backstop.yml --limit 6 --json conclusion,createdAt
# 代码抽验
sed -n '145,155p' backend/main.py
sed -n '18,28p' backend/realtime/log_writer.py
sed -n '95,127p' backend/tasks/saq_tasks.py
sed -n '235,250p' backend/services/notification_service.py
# 引用扫描：对 9 份文档执行相对链接存在性检查（联合全集：主干 + #1369 worktree）
```
