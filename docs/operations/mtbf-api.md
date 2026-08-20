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

**鉴权**：任意登录用户（只读校验，无写操作）；外部 agent 可用 `X-Agent-Secret` 或用户 token（双通道，见 `scripts.py:_try_verify_agent` 先例）。

**请求**（两种输入源，P0 语义写死）：

| 方式 | 内容 |
|------|------|
| multipart（主路径，不依赖磁盘可达性） | `file` = runtask.xml（必填）；`global` = UiAutomatorTestData.xml（可选，用于 `@@var` 引用校验） |
| JSON（仅控制面本地可达时） | `{"path": "<控制面可达路径>"}` |

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

## §1.5 P0：Agent 侧 `STP_MTBF_*` env 通道（部署说明，2026-08-20 定稿）

脚本配置解析顺序 = `STP_STEP_PARAMS` > `STP_MTBF_*` env > 代码默认（`_lib.py:param_or_env`；
平台无逐计划参数通道，ADR-0029 D1 挂起）。env 注入分两档：

| 键 | 通道 | 说明 |
|----|------|------|
| `STP_MTBF_EXPECTED_TESTPOINT_COUNT` | **fleet 同步**（`_FLEET_ENV_KEYS`，控制面 `.env.backend` 设置后 hot-update 下发） | `mtbf_check` 的 `expected_per_round`（0/未配置=只报绝对数）。全 fleet 同值；套件变更时改控制面一次 + hot-update |
| `STP_MTBF_TASK_TIMES` | **host 级手工 .env**（不在同步白名单） | 冒烟=1、生产=100（代码默认），未来相机套件按项目分化——故意不 fleet 同步。改后必须 `systemctl restart stability-test-agent.service` |
| `STP_MTBF_PROJECT` / `STP_MTBF_AUTO_RESUME` / `STP_MTBF_INSTALL_APKS` / `STP_MTBF_RESOURCES_DIR` | host 级手工 .env（可选） | 代码默认 `legacy` / `true` / `true` / 相对 Agent 目录 |

**hot-update 对 .env 的语义**：只合并白名单键 + 安装目录派生键，**保留**非白名单行（含手工 `STP_MTBF_TASK_TIMES`）；
`agent/resources/mtbf/` 已加入 rsync `--exclude`（2026-08-20 修复：此前 `--delete` 每次 hot-update 清空 MTBF APK）。
APK 三件套（`OfflineScriptManager.apk` + `ReliabilityUiautomatorTest.apk` + `ReliabilityUiautomatorTestTest.apk）
放 `/opt/stability-test-agent/agent/resources/mtbf/{project}/`，带外部署，sha 与源目录一致（`/mnt/automation-toolkit/android-tools/stability_MTBF-Test/apk`）。

**设备资格前置**：`mtbf_setup` v1.3.0 在 prefs 写入前校验 `adb root`（`id -u` 须为 0）；
user 构建（`ro.debuggable=0`）直接 fail-fast，需 userdebug/eng 工程包。

## §2 P1：用例集管理（占位）

`test_suite` / `test_case` CRUD + import / export / validate / export-to-tool-dir（约 13 个端点）。
决策见 [ADR-0030](../adr/ADR-0030-multi-case-suite-management.md) D2/D4；端点草案见研究 §5.5；
写权限模型（`X-Agent-Secret` 只读或限定 import/export）P1 评审定。

> **端点定稿后补本页**：curl 示例 + 权限矩阵 + 常见操作（导入既有 130 条 → 改 1 条 → 导出 → 派发，全程有审计）。
