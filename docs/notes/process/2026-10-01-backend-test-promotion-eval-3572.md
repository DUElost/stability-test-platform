# backend-test 前移评估（#3572：#1525 触发规则已响 4 次）

Status: proposed
Class: process
Issue: #3572
Related: #1525（触发规则来源）、#2333（flake 分类输入）、#3573（frontend 类归因缺陷）

## Decision

**选「信息性前移」——`backend-test` 进 PR 侧但**不阻塞**合入，观察一个周期后再议 required。**

**当前状态＝决策已定、CI 改动未做**（`ci.yml` 尚未把 `backend-test` 放进 PR 路径）。故本
note 状态为 `proposed`；实现（加 informational job + 采集 flake 率的埋点）是后续单独
一步，不在本单交付面内。下方「当前分层」一节据此标注为「已裁决·待实现」。

不选 required 的决定性理由不是成本，是**这个套件的 flake 率至今未知**：#2333 事实 3
明文记载「`backend-test` 类红灯里混有 flake」，而它的边界第三条同时写着「**无法断言
历史红夜中 flake 的占比**」。把一个 flake 率未知的检查直接设为 required，等于让
「required check 随机阻塞」成为常态——这恰是 #1525 当初驳回全量前移的同一条理由。

不选「维持现状」的理由：触发条件已明确响起且**四样本全部经重跑判定为确定性缺陷**
（见下表），敞口是「缺陷绿灯合入、次晨暴露」的有界损害，但它每周都在发生。

## 触发证据（09-12 裁决后至 09-27）

| 单 | 日期 | 失败 job | 重跑 | 分类 |
|---|---|---|---|---|
| #2628 | 09-19 | backend-test | failure | 确定性 |
| #2970 | 09-21 | backend-test | failure | 确定性 |
| #3060 | 09-22 | backend-test | failure | 确定性 |
| #3247 | 09-27 | backend-test | failure | 确定性 |

4/4 确定性，远超「≥2 次」阈值，且与 #1525 原始量化样本（`backend-test` 类占 5/6）
方向一致。

`frontend-check` 类不参与本次评估：其两次红灯（#1935、#2441）均未取得分类——#2441
单内自述「重跑未在预算内完成……不得作为前移评估的样本」，根因已由 #3573 修复，该类
需重新积累分类样本后另行评估。

## 实测数据（本轮现取，非引用旧结论）

| 项 | 实测值 | 取法 |
|---|---|---|
| `backend-test` 墙钟 | **16~60 min**（自身 `timeout-minutes: 60`） | run 35273011416 / 36781232110 的 job 时间线 |
| `frontend-check` 墙钟 | **≈2 min** | 同上（含 09-17 两次：2m09s / 2m14s） |
| PR 吞吐 | ≥200 PR / 7 天（`gh pr list` 上限截断，实际更高） | `gh pr list --state merged` |
| PR 侧现有 required 超时上限 | 30 min（`pr-agent-tests` / `pr-typecheck` / `pr-compileall`） | `.github/workflows/ci.yml` |

**关键推论**：`backend-test` 若直接进 required，会成为 PR 关键路径上**唯一超过 30 分钟**
的检查——现有 required 全部 ≤30min，它单独把形态拉高一倍以上。且 PR 吞吐 ≥28/天，
按 40 min 计约 18.7 runner-小时/天。

## 为什么是「信息性」而不是别的

信息性前移用一个观察周期换三样现在没有的东西：

1. **PR 路径上的 flake 率**——这是转 required 的必要输入，现在完全没有；
2. **PR 路径上的实际墙钟**——夜间全量与 PR 环境（并发、缓存、容器）不同，
   16~60 min 这个数字未必平移到 PR 侧；
3. **「本可拦住」的对照数据**——信息性检查会告诉我们，若设为 required，这 4 次确定性
   缺陷中能拦住几次、以及会误拦几次。

代价是这 4 次缺陷仍会照旧合入。但敞口有界（≤24h 暴露、且四单均已 CLOSED），
用一个周期换上述三项数据，代价可接受。

## 预注册的转 required 判据（避免下一轮又变成「再看看」）

观察期取**两个自然月**或**≥60 个 PR**（以先到者为准），届时期望值：

- PR 路径 flake 率 **< 1%** 且 PR 墙钟稳定 **≤30 min** → 提 PR 转 required；
- flake 率 ≥ 1% → **不转**，转入「去 flake」队列（#2333 边界第三条指出 flake 目前
  无沉淀路径，需一并处理）；
- 墙钟 >30 min 且无法通过分片降到 30min 内 → 不转 required，保持信息性，并记录
  「成本不可接受」作为结论。

届期无论结论如何，都必须在本节落一条记录（结论 + 数据），不允许静默续期。

## Alternatives

- **直接转 required**：否决。flake 率未知（#2333 事实 3 + 边界第三条），且会引入
  唯一超 30min 的关键路径检查。
- **维持现状**：否决。触发条件已响 4 次且全为确定性，每周都有确定性破坏绿灯合入。
- **引入 Merge Queue**：#1525 已驳（成本 ≈10× 全量 CI/天，数据不支持），本轮无新数据
  推翻它。
- **只前移 backend-test 的确定性子集**（如 `pr-agent-tests` 那样分片）：技术上可行，
  但需要先有「哪些子集对应那 4 次确定性失败」的数据——目前 4 单的失败用例名未被系统
  化沉淀（#2333 的失败用例名提取在部分 run 上取不到），故本轮不做。若观察期结束时
  仍不转 required，可回到这个方向。
- **先修 flake 再前移**：方向对但顺序不对——不前移就没有 PR 路径上的 flake 数据，
  死锁。

## Verification

- 触发证据：#2628 / #2970 / #3060 / #3247 四单的 #2333 归因块（首次与重跑均 `failure`）
- 墙钟：run `35273011416`（backend-test 16m19s、frontend-check 2m09s）、
  run `36781232110`（backend-test 40m00s、frontend-check 1m58s）
- 超时上限与分层：`ci.yml` 的 `backend-test` / `pr-agent-tests` 等 job 定义
- 规则原文：`docs/development/repository-workflow.md` §「夜间红灯的前移触发规则」

## Revisit

- 观察期届期（两个自然月或 ≥60 PR，先到者）必须回到本节落结论；数据来源为
  `backend-test` 的 PR 侧 run 历史与兜底单的 flake 分类计数。
- `frontend-check` 类的前移评估**独立于本单**，待 #3573 修复后重新积累分类样本。
- 若期间 `backend-test` 的墙钟或测试构成发生重大变化（拆分、并行化），本评估结论需重看。
