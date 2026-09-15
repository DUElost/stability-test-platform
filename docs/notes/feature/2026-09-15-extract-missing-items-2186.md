# 提取缺口可下钻：`extract.missing_items`（#2186）

Status: implemented
Class: feature

## Decision

`run_context.extract` 此前只有 `missing` **计数**：「缺失 3」说不出缺的是哪 3 个，现场只能回中心存储
手工比对——而这三条恰恰是本次交付真正要盯的东西（jira 目录里少的那几个事件）。本次补两键 + 前端下钻：

1. **后端 `services/dedup_extract.run_extract_sync`** 写 `run_context.extract` 时新增：

   | 键 | 语义 |
   |---|---|
   | `missing_items` | 缺失条目的**有界**清单（前 `_MISSING_ITEMS_MAX = 20` 条，去重，保持首次出现顺序） |
   | `missing_total` | 缺失总数（与既有 `missing` 同源——同一处累加，不会互相矛盾） |

2. **前端 `DedupReportCard`** 在流水线下方渲染缺口块：
   `提取缺失 N 项` + 逐条路径（mono、`title` 带全文）+ 截断时 `还有 N 条未列出（仅列前 20 条）`。

### 为什么"有界"是硬约束而不是优化

每条清单项都是**完整 NFS 路径**。无界清单会随 fleet 规模线性增长，而这个字段存在
`plan_run.run_context` 里、每次打开执行页都会被读出来——它会把一个诊断字段变成行级负担。
故截断在上限处，并**把总数单列**（`missing_total`），让"截断"与"就这么多"可区分。

### 为什么清单去重、计数不去重

`list_remote_paths_for_extract` **没有 DISTINCT**（同一 `remote_path` 可能挂在多条 DLE 行上），
所以重复是真实存在的。但两者口径必须分工：

- **计数**（`missing` / `missing_total`）**如实累计**——它是既有语义，不能因为清单去重而变小；
- **清单**去重只为可读（同一路径重复 5 次对现场排查毫无增量）。

### 前端为何就地收窄而不改 `types.ts`

`missing_items` / `missing_total` 尚未登记进 `frontend/src/utils/api/types.ts`：该目录正被在窗
Execution `fix-2051-2054` 声明，本 PR 不越界。组件侧用
`ExtractSummaryWithMissing = RunContextExtractSummary & { …?: unknown }` 就地收窄，
并且**运行时校验**（只收字符串、只认数组）——不盲信 `run_context` 里的 JSON。
正式登记留作声明释放后的收尾（见 Revisit）。

**涉及**：`backend/services/dedup_extract.py`（+ `backend/tests/services/test_dedup_extract.py`）、
`frontend/src/components/plan-run/DedupReportCard.tsx`（+ 用例）。**无契约变更**（`run_context` 是
自由 JSONB 段，新增键不影响任何响应模型；`dedup/status` 的响应形状未变）。

## Alternatives

- **无界清单**：**否决**。见上——诊断字段不能随 fleet 线性增长。
- **只存 basename**：**否决**。排查时要拿路径去中心存储里找/比，basename 在多层目录下不足以定位；
  界面已有 `truncate` + `title` 展示全文，无需为可读性牺牲诊断力。
- **前端自己扫中心存储找缺口**：**否决**（不可行）。前端没有 NFS 访问权；且"缺什么"的权威在提取
  那一刻的解析结果（`resolve_extract_event_src` 返回 None），事后扫描只能近似复现。
- **单开一个 `GET …/extract-missing` 端点**：**否决**（本轮）。为一个有界诊断清单加端点+契约+门禁
  登记，成本高于把两键放进已有的 `run_context.extract`。
- **落独立表**：**否决**（本轮）。只在"清单必须无界"时才成立（见 Revisit）。
- **计数也去重**：**否决**。会改变既有 `missing` 语义（且让"目标/已拷贝/缺失"三者不再自洽）。
- **前端不显示"旧数据无清单"分支**：**否决**。那会把"这次没记录"读成"没有缺口"——正是本条要消灭的歧义。

## Verification

- 后端：`pytest backend/tests/services/test_dedup_extract.py` → **21 passed**
  （改写 1 条既有用例断言 `missing_items`/`missing_total`；新增 2 条：
  ① 25 个缺失 → `missing_total == 25`、`len(items) == 20`、无重复且只来自真实缺口路径；
  ② 同一路径挂 2 行 → **计数 2、清单 1**（钉住"清单去重不改计数"）；
  `+ backend/tests/api/test_dedup_scan_endpoints.py` → **47 passed**）。
- 前端：`vitest run src/components/plan-run/DedupReportCard.test.tsx` → **10 passed**
  （新增 3：清单渲染、截断提示「还有 24 条未列出（仅列前 2 条）」、有缺口但无清单 → 「该次运行未记录清单」）；
  全量 **822 passed（106 files）**；`tsc --noEmit` 通过。
- 根 `tests/` → **1035 passed**；`run_gates check:pr` → **[OK] 18 gates**。
- **一次真实红灯（记录）**：两个新用例首跑 `NameError: name 'PlanRun' is not defined`——
  该测试文件对 `PlanRun` 是**函数内局部导入**的既有风格，我照抄后即绿（不是改断言绕过）。

## Revisit

- **`frontend/src/utils/api` 声明释放后**：把 `missing_items` / `missing_total` 正式登记进
  `RunContextExtractSummary`，并删掉组件里的 `ExtractSummaryWithMissing` 就地收窄
  （收窄是并发期的权宜，长期留着会让"未登记字段"合法化）。
- **若现场反馈 20 条不够**：不要提高上限把 `run_context` 撑大，改为**落独立产物**（例如提取报告
  文件 + 端点分页读），并同步 `missing_total` 的展示。
- **若缺口长期为 0**：可考虑把 `missing_items` 从 run_context 移到提取日志行，减少每页读取的字段数
  （当前保留是因为排查时"看得见"比"省一点体积"更重要）。
