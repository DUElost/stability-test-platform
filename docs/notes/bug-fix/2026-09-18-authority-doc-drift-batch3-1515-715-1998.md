# 权威文档漂移批三：barrier 判据、H-03 闭环、UNISOC 现状段（#715 / #1515 / #1998）

Status: implemented
Class: bug-fix

## Decision

三条**同一失效面**的回写：权威文档/台账陈述的事实已被代码或生产数据推翻，但文字未变。
共同点是——这类漂移**不会让任何测试变红**，只在有人按它行事时才伤人。

| 单 | 过期陈述 | 真值（file:line / 实测） |
|---|---|---|
| #715 | `docs/design/2026-08-step-stall-detection.md` §5 题为「后续阶段（**未实施**）」，且阶段 3 判据写作「看 peer 的 `last_progress_at` 是否在推进」 | `15a6bb45`（#872 Part 1，09-13）已落地；`pipeline_engine.py:1382-1412` 现判据是 **`EXECUTING_STEP` 执行态本身即活性证据**，戳新鲜度降级为诊断日志（`run 338` 误杀实证）。ADR-0026 旋钮行也缺绝对硬顶 `STP_BARRIER_MAX_WAIT_SECONDS=1800`（`:226`/`:235`，应用 `:1445-1456`） |
| #1515 | 四维度审查台账 §四 H-03 仍记「仍成立」，总表 #1520 行挂 `[ ]` | **#1520 已 CLOSED**；`plan_runs/agent_api/projects` = **487 / 391 / 360** 行（开单 3328/3239/997），防回流由 `check_god_files_ceiling.py` 的 `CEILINGS` 棘轮承担；举证 `docs/notes/bug-fix/2026-09-18-god-module-closure-1520.md` |
| #1998 | 正文「展锐无输入 → 事件停在 `LOCAL`/`UPLOAD_PENDING`」、「`deploy/prometheus` 无任何 UNIVIEW/UNISOC 规则」、P1 的 ⚠ 段（触发方式不可行） | 生产只读实测（09-18）：UNISOC `REMOTE` 660 / `ARCHIVED` **25** 条，推进耗时 **7–22 秒**（判据是 24h）；告警规则已由 **#2394** 落地两条：`deploy/prometheus/alerts-stability-platform.yml:304` `StabilityUnisocUnresolvedBacklog`、`:322` `StabilityUnisocDirAbandonedRegression`，并已有 promtool 场景（同目录 `.test.yml:371`）；⚠ 段在本单 09-15 评论已被推翻（事件确实产生，丢失在平台侧，已由 #2272 修） |

落点选择（**为什么这么改而不是删掉**）：

1. **§5 改成逐阶段状态表**，而不是把「未实施」划掉：每阶段给 ✅/🟡/⬜ + `file:line` +
   **可重算口径**。阶段 3 未做完的部分明写「属 #117 的裁决，不由设计稿单方面宣布」——
   回写不是替别人收口。
2. **计数一律附口径**，并显式记录本单踩到的坑：`grep -rl PROGRESS backend/agent/scripts` 得 24 族，
   换成更窄的 needle（只认 `_PROGRESS_PREFIX =`）得 **4**——同一族可有两种发射器写法，
   窄 needle 是**假阴性**。这与 #2639（`git grep 'tests/**/*.py'` 静默少算 81 文件）、
   #2643（`grep 'expr:'` 漏折叠标量）同形：**数字只能派生，且派生判据必须写出来**。
3. **ADR-0026 只补既成事实 + 修订记录一行**，不改决策、不升版本号；#715 的 barrier 语义
   归属仍在设计稿，ADR 侧只登记旋钮与兜底约束（避免与 §3.5「决策实体唯一性」打架）。
4. **审查报告不动 §四/§十 原文**，行内加 ⚠️ 指针 + 新增 §十一 回写节——沿用本报告对 R-01
   的既有范式（「保留原始计数以维持可追溯性」）。§十一 同时明写：**其余 12 条本次未重验**，
   且 §五 记的「台账无回写机制」这条**仍然成立**（本次是人工补写，不是机制），防止读者把
   本节误读成「回写已自动化」。
5. **#60 / #84 不并案**：只按实测事实各留一条评论（见 Verification），#60 的「不建议优先」
   前提与四模块拆法是否仍有价值属 owner 裁决，本单不替它关单。

## Alternatives

- **直接把 §5 整节删掉 / 改成「已实施」**：否决。阶段 2 后半（逐步骤开 `stall_seconds`）与
  阶段 4 确实未做，整节宣告完成是反向漂移。
- **在 #715 的 Epic 验收项上直接打勾**：否决。三条里只有「脚本输出 PROGRESS」有仓库侧证据，
  「构造 hang 场景验证 60s/120s 精准终止并释放 Permit」需真机；「`test_pipeline_engine.py`
  增补 stall 用例」的实际落点在 `test_coordinator_peers.py`（barrier 判据）与 stall 相关
  用例分散在 Agent 套件——照字面勾等于伪造证据。只回写事实与落点，勾选留给能实跑的人。
- **顺手把 ADR-0026 的 barrier 决策改写成「信任执行态」**：否决，那是 #872/#117 的裁决面，
  且会撞 §3.5 决策实体唯一性；本单只做旋钮与既成事实的回填。
- **加一条「报告条目 ↔ issue 状态」自动对拍门禁**：不在本单。issue 状态是网络事实，仓库内
  required check 不可达（离线门禁不能长网络依赖）；已在 §十一 写明这一边界与「触碰即回写」纪律。

## Verification

- `sed -n`/`grep -n` 逐条核对本 Note 表中的每个 `file:line`（tip `3aa0638e`）：
  `pipeline_engine.py:226,235,1382-1412,1445-1456`、`environment-variables.md:326/402`、
  `models/plan.py:109`、`clean_env/v1.1.0/_adb.py:75`、`gpu_check/v1.0.9/_lib.py:131`、
  `sleep_setup/v1.0.2/_lib.py:102`、`flash_firmware/v1.3.16/flash_firmware.py:691` 全部命中
- 计数复现：`grep -rl 'PROGRESS ' backend/agent/scripts --include=*.py | sed -E 's|…/([^/]+)/.*|\1|' | sort -u | wc -l` → 24；
  `ls -d backend/agent/scripts/*/ | wc -l` → 34
- 行数复现：`wc -l backend/api/routes/{plan_runs,agent_api,projects}.py` → 487 / 391 / 360；
  `tools/dev/check_god_files_ceiling.py` 四文件全绿
- `tools/dev/check_governance_surface.py --check`、`scripts/run_gates.py check:quick`（ruff /
  env-inventory / 链接与 DOC-MAP 面）、`pytest tests/ -q` → 见 PR 正文
- 未做（标 pending，不当作通过）：真机 stall 场景（60s/120s）与 permit 释放实测；
  #1998 P2「inotifyd 支持度/写盘时序」需真机，本单**不作判断**，只把「未复核」写进正文
- #60 事实：`backend/api/routes/agent_api.py` 391 行 / 19 个路由，仍单文件（无
  `agent_claims.py` 等四模块）→ 拆分**未做**，但其自述的成本前提（三千行热路径文件）已消失
- #84 事实：`grep -c "^class " backend/api/routes/agent_api.py` → **0**，schema 已在
  `backend/api/schemas/agent.py` → 本单目标已达成，待举证关单

## Revisit

- **台账回写的机制化**：本报告 §五 增量发现 4（「无状态回写机制」）仍未收口。可行的近似形态是
  「触碰即回写」+ 时点限定语；真正可机判的版本需要一个能读 GitHub 状态的离线入口，属治理面
  独立裁决，别在本单里顺手做。
- **#1998 P2 归档语义**：事实前提已变（25 条已到 `ARCHIVED`），剩下的真问题是「该终态对展锐
  意味着什么、与 MTK 的语义差异是否需写进文档」——措辞已按此改写，裁决点仍挂 #463。
- **#715 阶段 2 后半 / 阶段 4**：`stall_seconds` 逐步骤开启与内层钟收窄仍未做；本单只保证
  读者不会再被「未实施」四个字误导到已经改过判据的 barrier 上。
