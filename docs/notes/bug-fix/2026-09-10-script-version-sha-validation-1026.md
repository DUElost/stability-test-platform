# 脚本版本创建的 SHA 与路径契约（#1026）

Status: implemented
Class: bug-fix

## Decision

#1026（R08-F06）：新建版本表单只校验版本号，SHA 留空仍可提交；后端
`ScriptVersionCreate.content_sha256` 是普通字符串接受空值并创建活动版本——该版本
必然无法匹配实际文件（precheck `script_verify_failed`）；NFS 路径留空时静默复用
旧版本路径，扫描按路径找不到新版本目录会判磁盘缺失并**停用**脚本（R08-F06 双故障）。

修复（前后端同型契约，前端先拦、后端兜底）：

- **后端** `ScriptVersionCreate._check_version_contract`（422）：
  - `content_sha256` 必须 64 位 hex（`re.fullmatch`）——空 / 短 / 非 hex 全拒；
  - `nfs_path` 必须落在 `v{version}/` 目录段下——静默复用旧版本路径直接拒绝，
    与 script-versioning.md 的目录契约（`{name}/v{version}/entry`）对齐。
- **前端** `ScriptVersionDialog`：提交按钮 disabled 条件从「仅版本号非空」改为
  `version && shaValid(64 hex) && pathValid(含 /v{version}/)`；SHA / 路径字段标为
  必填并即时显示错误行；**移除 `nfs_path || script.nfs_path` 的旧路径回填**——
  那正是「静默复用旧路径」的入口。

与 ADR-0033 的关系：无直接约束（现行 ADR-0020/0021 版本域）；方向上与 D3
「DB catalog 唯一权威」一致——sha 入库即契约，本单是该方向的铺垫，无依赖。

## Alternatives

- 只做前端校验：绕过 UI 直接 POST 仍能造出必败版本——校验必须在 schema 层兜底；
- 后端只拒空 SHA 不校验 hex：`"zzz"` 同样必败，按验收标准「空或非法」一起拦；
- 路径一致性用「路径前缀 == 老路径改版本号」推导：隐式推导比显式契约脆，直接
  要求 `v{version}/` 目录段并与文档口径一致。

## Verification

- `pytest backend/tests/api/test_scripts.py`：27 passed，新增 4 例——空 SHA /
  短非 hex SHA / 复用 v1.0.0 旧路径（创建 2.0.0）各 422 且错误含对应字段 /
  合法 SHA+匹配路径 201 且激活；
- `test_scripts_default_params.py`：7 passed（既有版本创建流程不受新契约影响
  ——其用例路径本就符合 `v{version}` 布局）；
- 前端：`tsc --noEmit` 0 错误、`eslint ScriptVersionDialog.tsx` 干净（node_modules
  软链主 checkout）；
- ruff 全绿。

## Revisit

- `POST /scripts`（首次建脚本）未加同型校验——首版的路径契约同样应校验，本单
  按 issue 范围只收「新建版本」；若要统一，另立单（改动面含脚本创建表单）；
- 存量已入库的空 SHA / 错路径版本不在本单清洗——如需对账脚本（比对
  content_sha256 与磁盘实文件），另立单；
- 前端 `param_schema` 占位示例 `{"type": "int"}`（后端只收 `integer`）属
  #1029 文档/示例同步范围，本单未动。
