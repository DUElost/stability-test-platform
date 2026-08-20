# 本周提交审查采纳的两处修复（MTBF path 权限 + TimeoutInput 垃圾输入）

- **状态**：已实施
- **类别**：bug-fix
- **日期**：2026-08-20
- **关联**：MTBF P0（`docs/operations/mtbf-api.md` §1）、#312（TimeoutInput 后续）

---

## 背景与决定了什么

对最近一周合入 main 的提交做了一轮 bug 审查，共列 7 项候选，采纳 2 项修复：

### 1. MTBF `path` 输入源加 admin 门禁（安全）

`POST /api/v1/mtbf/runtask/validate` 的 JSON/Form `path` 输入源经
`_read_control_plane_path` 直接 `Path(path).read_bytes()`——任何登录用户
（`get_current_active_user`，非 admin）都能让后端读进程可读的任意文件
（`.env.backend`、`hosts.ini` 等），并通过 XML 解析结果 / 错误差异探测路径。

**修法**：仅 `path` 输入源要求 admin（新 helper `_ensure_admin_for_path`，
403 `PATH_READ_FORBIDDEN`，Form 与 JSON 两个入口都拦）；multipart 上传保持
任意登录用户——内容由调用方自带，不构成磁盘读原语，且这是 P0 设计文档
写死的契约（「任意登录用户」针对的就是上传校验）。

**放弃的备选**：
- 整端点 `require_admin`——会把文档契约里 multipart 的「任意登录用户」一起改掉，超出最小修复面；
- 路径前缀白名单（如限制在 `/mnt/automation-toolkit/` 下）——工具包路径没有 env 配置化，硬编码脆弱；且 admin 本就能在控制面 shell 读这些路径，白名单对 admin 无增益、对非 admin 已被 403 挡死。

### 2. TimeoutInput 非数字输入不再改写成 1 秒

`8b305ba`（#312）把超时输入改成 `Math.max(1, parseInt(raw, 10) || 1)`：
意图是「输 0 夹到 1」，副作用是 `parseInt` 为 NaN 的输入（实证可达的是
number input 的合法浮点文本 `".5"`——`parseInt('.5')` 是 NaN）被 `|| 1`
折成 1 秒提交，原超时值（如 45）被一次手滑毁掉。

**修法**：`Number.isNaN(n)` 直接 return 不提交，失焦回落到最后一次提交值
——与空值（`f84e04f`）同一套 draft 语义。`'0' → 1` 的 #312 行为保留
（`Math.max(1, 0)`）。

## 未采纳项及理由（审查结论存档）

| 候选 | 结论 |
|------|------|
| `bulk_assign_project` 静默跳过缺失 device_id | **不成立**（复核撤回）：`len(devices) != len(set(device_ids))` 已对缺失/重复 404 |
| results 按 project 过滤丢 `plan_run_id IS NULL` 行 | 语义合理（无归属），非 bug |
| socketio 房间白名单 | fail-closed 行为正确，无改动 |
| `PageSkeleton.Stats` 越界 throw | 有意的 fail-fast 防错，不改 clamp |

## 如何验证

- 后端：`backend/tests/api/test_mtbf_validate.py` 11 passed（新增 JSON/Form
  两入口的非 admin 403 用例；原 3 个 path 用例改用 `admin_headers`）；
  `ruff check` 通过；空行污染检查通过（短文件 skip）。
- 前端：`PlanStepInspector.test.tsx` 40 passed（新增 `.5` 不提交 + 失焦回落
  用例；jsdom 实证 `.5` 能到达 onChange 而 `'abc'`/`'e'` 被 number input
  消毒吞掉）；tsc / eslint 0 错误。

## 何时重议

- 若外部 agent 确需用**非 admin** token 走 `path` 输入源（当前无此调用方：
  前端不调该端点，ops curl 示例用 admin token），再评估 agent 专用通道
  （`X-Agent-Secret` 双通道，ops 文档原有此设想但实现从未落地）。
- `PATH_READ_FORBIDDEN` 的 403 不区分「文件存在与否」——非 admin 无法借该
  端点探测路径存在性，这是有意为之。
