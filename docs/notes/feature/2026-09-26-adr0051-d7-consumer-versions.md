# ADR-0051 D7：flashtool / aimonkey 消费族新版本声明 requires_tools（2026-09-26）

Status: implemented
Class: feature

## Decision

#3288 第 3 片（机制 #3365 已合入；工具条目 #3369）。四个消费族各发一个**只改声明**的补丁版本——
`capabilities.json` 增 `requires_tools`，代码不动（消费方本就读 `STP_FLASH_TOOL_DIR` /
`AIMONKEY_RESOURCE_DIR`，引擎注入包根即可）：

| 族 | 新版本 | 依赖 | 包 sha | 在用计划（只读核对） |
|---|---|---|---|---|
| `flash_firmware` | 1.3.18 | `flashtool@1.2444.00.100` → `STP_FLASH_TOOL_DIR` | `60b18725…` | 1.3.16：plan 11/12；1.3.17：39/40/59/61/62/68 |
| `flash_preflight` | 1.0.6 | 同上 | `d59f5f74…` | 1.0.5：12/39/40/59/61/62/68 |
| `monkey_resource_push` | 1.1.1 | `aimonkey@20260317` → `AIMONKEY_RESOURCE_DIR` | `b455c7e6…` | 1.0.0：2/6/21/22；1.1.0：33/55/67 |
| `monkey_test` | 1.2.3 | 同上 | `542f4e7a…` | 无启用步骤引用（入声明是为第 4 片撤 host-resources 通道时不留漏网族） |

- 流水线模板 `monkey.json` / `monkey_watcher_patrol.json` 按 #2865 同批钉到 `monkey_resource_push 1.1.1`。
- 四族在用版本的 `default_params` / `param_schema` 本就为空（只读查库；唯 `flash_preflight 1.0.1` 例外且无计划引用）
  ——scan 为新版本建空参数行不构成参数回退（#3289 形态不适用）。

**兼容性**：旧 Agent（无 #3365）忽略 `requires_tools`，走主机 `.env` 的既有键（#3234 注入 / 安装布局派生）
——新版本在旧 fleet 上行为不变。新 Agent 上依赖解析 **fail-closed**。

## Alternatives

- **顺手删 `flash_firmware` 的 `_DEFAULT_REL_FLASH_TOOL` 旧相对回退**：新版本走注入必命中 env 分支，回退成死代码；
  但删它要重测刷机解析链，本片保持「只改声明」的最小变更，死代码随第 4 片或下个功能版本清。
- **只发在用的三族、跳过 monkey_test**：第 4 片撤 host-resources 通道后 monkey_test 若被新计划引用会直接断；
  补一个只改声明的版本成本极低。

## Verification

- 四个包各自解开核对：`capabilities.json` 在包内且 `requires_tools` 正确（见上表 sha）；`check_script_packages`
  35 族树⇄最新登记等价、`requires_tools` 引用解析绿；`check_tool_manifest --base origin/main` append-only 绿
- 踩坑自证：`--register` 按 **`git ls-files` 已跟踪文件**取成员——新建未 `git add` 的 `capabilities.json` 被排除，
  首次登记的 `monkey_test@1.2.3` 恰好不含声明（sha `a43061be`）；提交后门禁判「改树未发版」当场暴露，分支内重登记为
  `542f4e7a`（未合入未发布，相对 main 仍 append-only）
- `tests/` 中涉及四族与模板的 136 passed；agent 侧相关 269 passed；`check:quick` 17 gates OK

## Revisit（上线顺序——**硬约束**）

1. **先发布工具包**（#3369 合入后立即）：`package_tool_asset … --write-manifest --packages-root /mnt/stp-aee/packages`
   两族——必须早于任何「带 #3365 的 Agent 载荷」上 fleet，否则新 Agent 执行这些版本即 exit 2。
2. 本片合入后：控制面 scan 建四个新版本行 → `check_script_packages --publish` 发布四个脚本包。
3. 真机取证（需 fleet 已是带 #3365 的载荷，部署节奏随 #3359 负责方）：一台 canary 跑 preflight 1.0.6 + flash 1.3.18、
   monkey_resource_push 1.1.1——核对步骤 env 为 `tools_cache/flashtool/…` / `tools_cache/aimonkey/…`、
   `required_tool_injected` 日志、首轮预热 NFS 流量（每台 ≤131MB，一次性）。
4. 计划重指（生产数据写，`PUT /plans/{id}` 带 `expected_updated_at`）：上表计划逐个重指；**plan 68 同时删步骤参数
   `flash_tool_dir`**（#3232 过渡绕行，param 优先级高于注入）。
5. 第 4 片：删 `STP_FLASH_TOOL_DIR` hot-update 注入（台账 `flash-tool-dir-env-injection`）与 host-resources 通道；
   主机 `.env` 残留行的处理见 #3356。
