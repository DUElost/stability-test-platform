# Suite 写入边界加固：export_dir 契约 / exec_descs 校验 / 停用守卫统一（#968 #969 #971，R05-F05/F06/F08）

Status: implemented
Class: bug-fix

## Decision

三个同面缺陷（`test_suite` / `test_case` 写入边界）一次收口，原则是
**写边界拒绝不可消费配置，读边界兜底 4xx 而非 500**：

1. **export_dir 目录契约（#968）**：新增 `normalize_export_dir`
   （`backend/api/schemas/suite.py`）——相对存储根的目录名 / 相对子路径；
   拒绝绝对路径、`..` 组件、空串与 NUL，其余规范化（折叠 `.`、去尾斜杠）。
   `TestSuiteCreateIn` / `TestSuiteUpdateIn` 字段校验（422）；
   `export-to-tool-dir` 写盘前对库内值复核同一函数（存量数据绕过 schema 的
   第二道闸），非法 → 422 `EXPORT_DIR_INVALID`，不落任何文件。
   默认链路（`resolve_export_dir` 回退 `project_key` / `legacy`）均为单组件
   相对名，不受影响。
2. **exec_descs 字段级校验（#969）**：写边界 `TestCaseIn` 校验元素形状——
   对象、`times` 可转 int 且 ≥1（缺省/空串按 1）、`args` 为对象；其余键
   原样存储（UI 是 JSON 编辑器，不重写用户数据）。读边界
   `exec_desc_from_dict` 对非对象显式 `ValueError`，`suite_from_rows` 补
   case 上下文冒泡，routes `_built_suite_or_422` 在 validate / export /
   export-to-tool-dir 三入口转 422 `EXEC_DESC_INVALID`。
   `times=0`/负值拒绝：0 会被读路径静默改成 1、负值原样渲染进 XML，均属
   「可消费但语义失真」，不收。
3. **停用守卫统一（#971）**：抽 `_reject_if_bound_runs`（复用 #402 守卫），
   DELETE 与 PUT `is_active=false` 共用同一 409 `SUITE_RUNS_ACTIVE`；
   导出守卫一并复用，三个入口不再各自维护重复报文。PUT 修改其他元数据
   不受在途 Run 阻塞（守卫只覆盖「停用」语义）。

## Alternatives

- **Pydantic 嵌套模型（`ExecDescIn`）替代手写校验**——放弃：UI 是
  `Record<string, unknown>[]` 的 JSON 编辑器，闭模型要么 `forbid` 额外键
  （用户输入直接 422，超出本单范围）要么 `ignore` 静默丢键（数据损失）；
  手写校验只承诺「读路径可消费」，其余键原样保留，最小且无副作用。
- **export_dir 只做 schema 校验、不做导出侧复核**——放弃：存量坏值（旧数据/
  直写库）仍会在导出时越出根目录；写盘前复核是同一契约的兜底闸。
- **只允许单级目录名（禁 `/`）**——放弃：相对子路径无越界风险且不排除合法
  分组用法；拒绝面收敛在绝对路径与 `..`。
- **读边界静默兼容（类型强转成功即放行）**——放弃：验收明确要求存量坏数据
  返回 4xx；显式报错才能定位坏行。
- **`normalize_export_dir` 放 `services/suite_binding.py`**——放弃：schema
  层跨包导入 services 破坏现有分层（schemas 现状不依赖 services）；契约
  函数放 schema 模块，导出侧复用。

## Verification

实际运行（worktree `/tmp/stp-r05`，2026-09-11）：

- `pytest backend/tests/api/test_mtbf_suite_routes.py -q` → **74 passed**
  （新增 14 例：export_dir 五类非法写入 422、更新 422、相对目录归一化与导出
  落点、存量绝对路径导出前 422 且不落盘、exec_descs 四类非法形状 422、
  数字字符串 times 照常、存量坏数据 validate/export/export-to-tool-dir
  全 422、PUT 停用 409 与元数据修改不受阻）；
- `pytest backend/tests/services/test_suite_binding_gate.py
  backend/tests/api/test_mtbf_validate.py
  backend/tests/services/test_test_case_result_ingest.py -q` → 均通过；
- `pytest tests/ -q` → **114 passed**；
- `ruff check .` → All checks passed；
- `check:quick` → **7 gates 全绿**。

未完成（pending）：

- 真实 NFS/共享存储导出演练：本机为生产控制面宿主，不触碰共享存储；
  越界拒绝与相对目录落点已由 tmp_path 夹具覆盖。

## Revisit

- 若 export_dir 未来需要多级命名空间/租户前缀的正式契约，改为显式约定并
  同步 `normalize_export_dir` 与文档；
- 若 exec_descs 新增字段级语义（如 apk 须与 `apk_binding` 一致），在
  `_validate_exec_descs` 同处扩展；
- #976（门禁与物化配置窗口）为并发侧风险，不在本单范围——需受控并发验证，
  本次未动。
