# 响应形状契约：手搓 dict 响应 ↔ `types.ts` 声明的机械对拍

Status: implemented
Class: testing

## Decision

新增 `tests/test_api_response_shape_contract.py`（**纯静态**：只读两个源文件，无 DB、无网络），
把「同一个响应形状的两处独立声明」按**双向判据**机械对拍：

1. **后端每个 `return` 字典的键 ⊆ TS 字段集** —— 返回了前端没声明的键，消费者看不见；
2. **TS 字段集 ⊆ 后端所有 `return` 字典键的并集** —— 声明了永不返回的键 = 幽灵键。

**逐分支包含**（判据 1）是必须的：只看并集拦不住「某个分支返回了额外键」，
而 `abort_plan_run` 恰恰是多分支、各分支键集合不同的形状（`QUEUED/PRECHECK` 分支少一个
`abort_requested_jobs`）。

覆盖方式：显式登记表

```python
_PAIRS = (("backend/services/plan_run_abort.py", "abort_plan_run",
           "frontend/src/utils/api/types.ts", "PlanRunAbortResult"),)
```

外加一条**登记表自证**用例（每对都要能解析出「≥1 个字典 return」与「≥1 个顶层字段」）——
否则改名/删除任一侧会让对拍**静默变空**，那就是对空集恒真的假守卫。

### 为什么是这一对、这个形态

`#787`（删 6 个幽灵键）与 `#2089`（`released_leases` 恒为 0 却两侧承诺）**两次漂移都发生在
`PlanRunAbortResult` ↔ `abort_plan_run` 这一对上**，且都属「两处声明漂移」而非代码故障：

- `tsc` 放行「声明了后端永不返回的键」（可选字段声明不报错）→ 幽灵键；
- `tsc` 也放行「后端返回了未声明的键」→ 新字段对消费者不可见。

放在**根 `tests/`**是有意的：它在 `pr-agent-tests` 里跑，因此**在 PR 路径上就拦住**
（对照：锁序回归需要 `#1999` 专门接线才进 PR 路径）。它是纯离线测试，符合根 `tests/` 的
离线纯度约束（`tests/test_offline_subset_guard.py`）。

### 轴线 C：Pydantic 响应模型 ↔ TS 接口（2026-09-15 追加）

原覆盖只认**手搓 dict** 的 `return` 字面量。日志链的响应多数是 **Pydantic 模型**
（`response_model=ApiResponse[X]`），它们同样有「两处独立声明」，此前**没有任何轴线覆盖**。
这不是假设：`WatcherPlatformBucketOut` 新增 `reconciler_supported` 时，后端不补 TS 也不会被
任何门禁拦住（`tsc` 因可选而放行）。同一文件同一机制扩展出轴线 C：

| 组 | 配对（字段数两侧一致） |
|---|---|
| watcher-summary 3 | `WatcherSummaryOut`↔`WatcherSummary`(19)、`WatcherPlatformBucketOut`↔`WatcherPlatformBucket`(7)、`WatcherCategoryOut`↔`WatcherCategory`(6) |
| log-events 2 | `PlanRunLogEventOut`↔`PlanRunLogEvent`(13)、`PlanRunLogEventsOut`↔`PlanRunLogEventsPayload`(4) |
| scan/merge 3 | `DedupStatusOut`↔`DedupStatusPayload`(4)、`DedupArtifactOut`↔`DedupArtifact`(6)、`DedupScanArchiveOut`↔`DedupScanArchive`(4) |

解析规则（`_pydantic_model_fields`，判据与轴线 A 相同：双向包含）：

- 取值**注解字段名**（`AnnAssign`），非运行期反射；
- 基类字段**仅在同一文件内**递归展开；遇到文件外、且非 `BaseModel` 的基类**直接报错**——
  静默少收字段会让「幽灵字段」判据假绿，那正是本门禁存在的意义；
- `Field(alias=…)` **不处理**：登记前提是该模型无别名（有别名时对拍的是 Python 字段名而非
  线上键名，会给出假绿）。

第 3 组来自一次**正规化**：`GET /plan-runs/{id}/dedup/status` 原为
`response_model=ApiResponse[dict]` + `ok({...})` 手搓 dict——轴线 A 的 AST 判据只认
`return <Dict>`，看不见包在 `ok(...)` 里的字面量，故它此前**任何轴线都覆盖不到**。
改为 `ApiResponse[DedupStatusOut]`（新增按域分文件的 `backend/api/schemas/dedup.py`）后，
形状变为「一处声明 + 机器对拍」。**这比扩展正则去识别 `ok({...})` 更可靠**：后者仍有盲区，
前者让端点逐个退出该盲区。

`DedupScanArchiveOut` 用 `extra="allow"` 是这一组的关键约束：`run_context.archive` 是自由
JSONB，Pydantic 默认**丢弃**未声明键——那会把「后端新增字段」变成静默消失，比不建模更糟。
已加「未知键必须透传」用例并做红绿双向（去掉 `extra="allow"` → `KeyError: 'future_key'`）。

## Alternatives

- **TS ↔ Pydantic 静态对拍**（原 #787 设想的「跨语言契约测试」）：**否决**。漂移发生在
  **手搓 dict** 上——`abort_plan_run` 返回 dict、不是 Pydantic 模型，所以对 Pydantic 模型做
  对拍**一次也抓不到** `#787`/`#2089` 这两次。方向选对了比工具高级更重要。
  - **2026-09-15 补充（不作为对上述否决的反转）**：该否决**仍然成立且未被推翻**——
    `abort_plan_run` 至今是手搓 dict，用 Pydantic 对拍照样抓不到那两次漂移。本单追加的
    **轴线 C 是并存，不是替换**：它覆盖**另一类形状**（Pydantic 响应模型），与轴线 A 各管
    一边。两条合起来只说一件事——**漂移的载体决定需要哪种轴线**，而不是「Pydantic 对拍是错的」。
    据此改写本 note 的旧决定会失去「为什么当年先做 A」这条信息，故仅追加不删改。
- **由后端响应生成 TS 类型**（单一真相源）：**本轮不做**。它会改前端工作流（类型将失去手写
  注释，如本次用到的「后端权威键」说明），且当前登记表只有一对——先有数据再决定。
- **运行时形状测试**（调一次接口、断言键集合）：作为**主机制**否决（需要 app+DB → 只能进
  夜间；且「声明了永不返回」这一向需要穷举所有分支，运行时很难覆盖）。作为**补充**可行，
  见 Revisit。
- **自动发现配对**（扫出所有端点与 TS 类型的映射）：**否决**。两者之间没有机器可读映射，
  硬做出来的发现器会退化成恒真守卫。
- **引入完整 TS 解析器**（typescript/ts-morph）：**否决**。为「声明面」的字段名引入一门语言的
  解析器不划算；受限解析 + 登记表自证 + 三向负向对照已足够，且失败信息直接给出键名。
- **把判据放宽成「子集即可」**：**否决**。放宽后幽灵键会重新隐形——那正是 `#787` 要治的病。

## Verification

| 项 | 结果 |
|---|---|
| 正跑 | **3 passed** |
| 负向对照 A：后端某分支多返回一个键 | 红：`abort_plan_run 的某分支返回了 PlanRunAbortResult 未声明的键 ['extra_probe']（消费者看不见）` |
| 负向对照 B：TS 声明一个永不返回的字段 | 红：`PlanRunAbortResult 声明了 abort_plan_run 永不返回的键 ['ghost_probe']（幽灵键，tsc 会因可选而放行）` |
| 负向对照 C：接口改名（登记表过期） | 红：`types.ts 里找不到 interface PlanRunAbortResult（登记表过期？）` |
| 根 `tests/` | **728 passed** |
| `ruff` / 治理面 / 差异面不变量 | All checks passed / `[OK]` / `[OK]` |

轴线 C（2026-09-15 追加）的验证：

| 项 | 结果 |
|---|---|
| 正跑 | **12 passed**（轴线 A 1 对 + 轴线 B 2 例 + 轴线 C 8 对） |
| 负向对照 D：从 `types.ts` 删除 `reconciler_supported` | 红：`WatcherPlatformBucketOut 的字段 ['reconciler_supported'] 未在 WatcherPlatformBucket 声明（消费者看不见）` + canary 同红 |
| 负向对照 E：去掉 `DedupScanArchiveOut` 的 `extra="allow"` | 红：`KeyError: 'future_key'`（Pydantic 静默丢弃未知键） |
| 根 `tests/` / `backend/tests/{api,services,tasks,core}` | **925 passed** / **2209 passed** |
| 前端 `vitest run` / `tsc --noEmit` | **813 passed（106 files）** / 通过 |
| `check:pr` / `check:quick` | **[OK] 18 gates** / **[OK] 10 gates** |

强制力来源：根 `tests/` 离线子集在 `scripts/run_gates.py:227-236`（对应 CI `pr-agent-tests`），
故轴线 C 与轴线 A/B 一样**在 PR 路径上生效**，不是只在本地手跑。

即在**当前**两处声明一致的前提下，三种破坏各自变红、恢复后复绿 —— 守卫对本类漂移有
双向鉴别力。

## Revisit

- **登记表的增长判据**：只登记「已发生过漂移」或「声明面较大且手搓」的配对。若登记表超过
  约 5–8 对、或出现需要第三种语言/生成式的诉求，再评估「由后端生成 TS 类型」这条单一真相源
  路线（本单 Alternatives 里缓做的那条）。
  - **该阈值已触及（2026-09-15）**：轴线 C 落地后登记表为 **9 对**（A 1 + C 8），进入原设阈值
    区间。本轮仍不转生成式路线，理由：9 对均为一行的机械登记、边际成本仍低；且新增的
    8 对刚在一次真实漂移（`reconciler_supported`）上证明了自己先于生成式路线产生了净收益。
    **新触发条件**：登记表 > 12 对，或出现需要同步的**嵌套 / 联合类型**（当前只比顶层字段名，
    嵌套结构两边各自展开、不校验）。届时按「后端模型 → TS 类型生成」单独提案，并按"决策实体
    唯一性"先查重。
- **未覆盖的一类声明面**：后端函数 **docstring** 里也列了键（如 `abort_plan_run` 的
  Returns 块，当前与实现对得上），本单不查它。它同样会漂移，但形态不同（带类型注解）；
  若再出现「docstring 承诺 > 实现」，就把它纳入同一登记表（多一个解析器分支）。
- **运行时补充**：若将来出现「后端按条件动态拼键」的形状（静态解析会退化），则补一条运行时
  形状测试（调接口 → 断言 `observed ⊆ declared`）作为静态判据的补充，而不是替代。
- **与 `#787` 的兜底关系**：`tsc` + knip + 评审逐项来源注明仍是第一层；本契约是第二层，
  只覆盖登记在册的配对，不能替代前端自身的编译期检查。
