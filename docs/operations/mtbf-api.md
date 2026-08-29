# MTBF 用例管理接口说明

- 日期：2026-08-19
- 定位：MTBF 专项在平台上的**管理接口运维说明**。§1 为 **P0 已定稿**端点；§2 为 **P1 占位**（端点定稿后补）。
- 真源：OpenAPI（控制面 `/docs` / `/openapi.json`）——本页提供 curl 示例与权限说明，字段以 OpenAPI 为准。
- 上游：[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md) D2/D4（决策）、[P0 设计 §5.1](../design/2026-08-mtbf-p0-runner-design.md)（校验规则）、[研究 §5.5](../reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md)（P1 端点草案）。

## §1 P0：runtask.xml 预览/校验（已定稿，随 P0 实施 PR 落地）

```
POST /api/v1/mtbf/runtask/validate
```

**用途**：上传 runtask.xml（可附 `UiAutomatorTestData.xml`）返回结构化预览与校验问题清单；外部 agent / 人工在派发前确认清单正确。

**鉴权**：multipart 上传 = 任意登录用户（只读校验，无写操作）；`path` 控制面路径输入 = **仅 admin**（该输入源是控制面磁盘读原语，非 admin 一律 403 `PATH_READ_FORBIDDEN`）。

**请求**（两种输入源，P0 语义写死）：

| 方式 | 内容 |
|------|------|
| multipart（主路径，不依赖磁盘可达性） | `file` = runtask.xml（必填）；`global` = UiAutomatorTestData.xml（可选，用于 `@@var` 引用校验） |
| JSON（仅控制面本地可达时；**仅 admin**） | `{"path": "<控制面可达路径>"}` |

**200 响应字段**：

```json
{
  "valid": true,
  "issues": [{"severity": "error|warning", "code": "...", "message": "...", "testpoint": "..."}],
  "preview": {
    "suite_name": "...",
    "root_config": {"times": 1000, "testTimeOut": 259200000, "...": "..."},
    "global_refs": ["@@gWifiName", "@@gWifiPwd"],
    "testpoints": [{"name": "...", "times": 1, "exec_descs": [{"class": "...", "method": "...", "args": {"wifiName": "@@gWifiName"}}]}]
  }
}
```

**校验规则**：XML 良构 / testpoint 名唯一 / method 非空 / `@@var` 引用在 global 文件有定义 / testcase schema（完整规则见 P0 设计 §5.1）。

**curl 示例（multipart）**：

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  -F "file=@runtask.xml" -F "global=@UiAutomatorTestData.xml" \
  http://<control-plane>:8000/api/v1/mtbf/runtask/validate
```

**curl 示例（控制面路径）**：

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path": "/mnt/automation-toolkit/android-tools/stability_MTBF-Test/config/runtask.xml"}' \
  http://<control-plane>:8000/api/v1/mtbf/runtask/validate
```

## §1.5 P0→P1b：MTBF 脚本配置通道（2026-08-24 随 ADR-0030 P1b 更新）

`mtbf_*` 脚本配置解析 = `STP_STEP_PARAMS` > `STP_MTBF_*` env（host 级手工）> 代码默认
（`_lib.py:param_or_env`）。expected/project 两键由**托管绑定自动注入**：
Plan 绑定套件（`plan.suite_id`）时，dispatcher 对 `mtbf_*` 步骤自动注入
`expected_testpoint_count`（= 套件启用用例数）与 `project`（= 套件 export_dir），
**无需任何 env 或 default_params 声明**。**绑定对 mtbf 脚本为强制**
（ADR-0030 v1.8）：未绑定 mtbf 计划派发即 400 `SUITE_BINDING_REQUIRED`；
非 mtbf 计划不受影响。

| 键 | 通道 | 说明 |
|----|------|------|
| ~~`STP_MTBF_EXPECTED_TESTPOINT_COUNT`~~ | **已退役**（ADR-0030 P1b：摘出 `_FLEET_ENV_KEYS`，hot-update 不再下发） | 托管模式由注入替代。`mtbf_check` ≥v1.3.0 只读注入、不再回落本键；≤v1.2.0 旧版仍读 env——无注入时回落默认 0（只报绝对数，安全降级）。host `.env` 中历史残留行对绑定 Run 无影响（注入优先），可顺手删除 |
| `STP_MTBF_TASK_TIMES` | **host 级手工 .env**（不在同步白名单） | 冒烟=1、生产=100（代码默认），未来相机套件按项目分化——故意不 fleet 同步。改后必须 `systemctl restart stability-test-agent.service` |
| `STP_MTBF_PROJECT` / `STP_MTBF_AUTO_RESUME` / `STP_MTBF_INSTALL_APKS` / `STP_MTBF_RESOURCES_DIR` | host 级手工 .env（可选） | 代码默认 `legacy` / `true` / `true` / 相对 Agent 目录；绑定套件的 Run 由注入的 `project` 覆盖 |

**双层退役语义**（ADR-0020 版本不可变的直接推论）：控制面摘键（本层）+ 新脚本版本
移除读取（`mtbf_check` v1.3.0）。已发布 v1.2.0 的 param_or_env 读取改不掉；
引用它的 Plan 升级步骤版本前，行为回落为「env 缺失 → 只报绝对数」。

**hot-update 对 .env 的语义**：只合并白名单键 + 安装目录派生键，**保留**非白名单行
（含手工 `STP_MTBF_TASK_TIMES` 与退役键的历史残留行）；
`agent/resources/mtbf/` 已加入 rsync `--exclude`（2026-08-20 修复：此前 `--delete` 每次 hot-update 清空 MTBF APK）。
APK 三件套（`OfflineScriptManager.apk` + `ReliabilityUiautomatorTest.apk` + `ReliabilityUiautomatorTestTest.apk`)
放 `/opt/stability-test-agent/agent/resources/mtbf/{project}/`，带外部署，sha 与源目录一致（`/mnt/automation-toolkit/android-tools/stability_MTBF-Test/apk`）。

**设备资格前置**：`mtbf_setup` v1.3.0 在 prefs 写入前校验 `adb root`（`id -u` 须为 0）；
user 构建（`ro.debuggable=0`）直接 fail-fast，需 userdebug/eng 工程包。

## §1.6 P0：中心存储布局与凭据警示（2026-08-20）

`{STP_AEE_NFS_ROOT}/mtbf/{project}/`（生产当前 `legacy`）：

| 路径 | 内容 |
|------|------|
| `runtask.xml` / `UiAutomatorTestData.xml` | 派发源，由工具链同步（`/mnt/automation-toolkit/android-tools/stability_MTBF-Test/config`） |
| `results/{run_dir}.json` | `mtbf_finish` 逐条结果（P2 `test_case_result` 数据源，不扩 artifact 白名单） |

> **凭据警示**：`UiAutomatorTestData.xml` 含**真实 SIM/WiFi/Google 账号凭据（明文）**，且该目录是常规运维可达路径。
> 禁止将其内容复制进仓库 / 日志 / PR diff / Agent Note；需要夹具或示例时一律脱敏（仓库
> `backend/agent/tests/fixtures/mtbf/` 已有脱敏样例，`.gitattributes` 标 `-text` 字节级快照）。
> 与 AGENTS.md「只文档化位置、不复制明文」体例一致。

## §2 P1：用例集管理（已上线，随 ADR-0030 v1.3 定稿）

`test_suite` / `test_case` 管理面 14 个端点（OpenAPI 为字段真源，本页给权限矩阵与常用流）。

**鉴权**：读 = 任意登录用户；写（POST/PUT/DELETE/import/export-to-tool-dir）= **仅 admin**。
全部写操作 `record_audit`（ADR-0015）。

### 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/test-suites` | 列表（含 `export_dir` / `case_count` / `exported_sha256` / `is_active`） |
| POST | `/api/v1/test-suites` | 建套件；name 冲突 409；未知 `project_key` 404 |
| GET | `/api/v1/test-suites/{id}` | 详情（含 `content_sha256` 与 `export_stale` 漂移标志） |
| PUT | `/api/v1/test-suites/{id}` | 更新（display_name / root_config / project_key / apk_binding…） |
| DELETE | `/api/v1/test-suites/{id}` | 软删（`is_active=false`）；绑定本套件的 ACTIVE Run 在飞时 409 `SUITE_RUNS_ACTIVE`（#404 精确守卫） |
| GET/POST | `/api/v1/test-suites/{id}/cases` | 用例列表 / 新增（suite 内重名 409） |
| PUT/DELETE | `/api/v1/test-cases/{case_id}` | 整覆盖更新 / 删用例 |
| POST | `/api/v1/test-suites/{id}/import` | multipart `file`=runtask.xml（可选 `global`=UiAutomatorTestData.xml），整体替换入库，记 `source_sha256` |
| GET | `/api/v1/test-suites/{id}/export` | 渲染 runtask.xml；响应头 `X-Export-Stale: 1` = 库已改未导出 |
| GET | `/api/v1/test-suites/{id}/global` | 渲染 UiAutomatorTestData.xml |
| POST | `/api/v1/test-suites/{id}/validate` | 校验**库内**数据（与 §1 文件输入分工） |
| POST | `/api/v1/test-suites/{id}/export-to-tool-dir` | admin。原子写两文件到 `{STP_AEE_NFS_ROOT}/mtbf/{export_dir}/` 并记双漂移基线 |

### 关键语义

- **双漂移检测器**：`export_stale` 由库内容指纹计算得出（任何写路径都不清快照列）——「库改了没导出」在 export 响应头与详情 `export_stale` 同时可见；「导出后磁盘被手改」由门禁比对 `exported_sha256` 捕获（precheck 五步门禁 `suite_verify_failed`，#404 PR-C）。
- **在途守卫（#402 / #516）**：绑定**同一套件**的 QUEUED/PRECHECK/RUNNING PlanRun → 409 `SUITE_RUNS_ACTIVE`。跨套件并发导出互不阻塞。未绑定 mtbf 存量 Run 不再阻断 export（#404 硬拒新派发后宽匹配已删）。
- **export_dir 解析**：显式 `export_dir` > 项目 key > `legacy`（兼容 P0 部署）。

### 典型闭环（ADR-0030 D6 验收信号）

```bash
# 1. 导入既有 130 条（multipart）
curl -sS -H "Authorization: Bearer $TOKEN" \
  -F "file=@runtask.xml" -F "global=@UiAutomatorTestData.xml" \
  http://<control-plane>:8000/api/v1/test-suites/$SUITE_ID/import

# 2. 改 1 条用例
curl -sS -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"tp_001","ordinal":1,"times":2,"enabled":true,
       "exec_descs":[{"class":"C","method":"m","args":{}}]}' \
  http://<control-plane>:8000/api/v1/test-cases/$CASE_ID

# 3. 校验 + 导出落工具目录（绑定套件有 Run 在飞时此处硬 409）
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://<control-plane>:8000/api/v1/test-suites/$SUITE_ID/validate
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  "http://<control-plane>:8000/api/v1/test-suites/$SUITE_ID/export-to-tool-dir"
```

### CLI（P1c：`tools/dev/mtbf-cases.py`，走同一 REST、同一审计）

```bash
# 凭据三级回退：--token > ambient STP_ADMIN_USER/STP_ADMIN_PASSWORD > 仓库根 .env.backend；明文不进输出
python tools/dev/mtbf-cases.py list [--project MTBF-MLD]
python tools/dev/mtbf-cases.py show --suite MTBF-legacy [--case test_Reliability0141_CloseStoreWlan]
python tools/dev/mtbf-cases.py import --suite MTBF-legacy --file runtask.xml [--global UiAutomatorTestData.xml]
python tools/dev/mtbf-cases.py export --suite MTBF-legacy --out runtask.xml [--times 100]
python tools/dev/mtbf-cases.py validate --suite MTBF-legacy
python tools/dev/mtbf-cases.py export-to-tool-dir --suite MTBF-legacy [--force]
```

退出码：0 成功 / 2 本地错误（套件或用例找不到）/ 3 远端拒绝（401/403/404/409 原样透出）。
`--base-url` 默认 `STP_BASE_URL` 或 `http://127.0.0.1:8000`。

> **待办**：`X-Agent-Secret` 双通道写权限（初版保守，开放条件见 ADR §7 #1）；真机冒烟
> （init trace `suite_sha256` == 门禁比对 sha，ADR-0030 D6 总验收信号）。
