# ADR-0051 Phase 4a：展锐两族入包登记 + 泛化包引用 resolver（逃生阀默认关）（2026-09-23）

Status: implemented
Class: feature

## Decision

把 #3075 第一切片的包机制从「Start-Log-Scan 一族 + scan_runner 单入口」推广到**任意工具族**，
并为展锐另两族完成登记与发布——机械面落地，fleet 不切换（与 #3075 同节奏）：

1. `tool_cache.resolve_packaged_tool(ref_env_key, env)`：泛化 resolver，**每族一个独立引用键**
   （空 = 该槽 no-op，C6 同款逃生阀）；`resolve_packaged_scan_tool` 保留为委托 wrapper，行为不变。
2. `UnisocScanRunner.configure` 两槽接包面，优先级 **显式传参 > 包引用 > env 路径键**；
   新键 `STP_UNISOC_LOG_SCAN_PACKAGE_REF` / `STP_UNISOC_SCAN_RESULT_PACKAGE_REF`
   （控制面源键 `STP_AGENT_` 前缀进 `_AGENT_SCOPED_ENV_KEYS`，空值不推）。
3. 登记 + 发布：`Scan-Result-GT@2026.09.23`（40 KB / 21 文件）、
   `Monkey-Log-Scan-GT-SPRD@2026.09.23`（39.5 MB / 43 文件，`log/` 运行态进排除面——
   打包器排除目录补 `log` 名）；两包已写 `/mnt/stp-aee/packages/`，站点 `manifest.json`
   副本派生（213 条目），Git `tool_manifest.json` append-only 绿。
4. `package_tool_asset.py` 加 `--python-absent`：外部工具族登记 `python: null`
   （包内无解释器 → Agent 自身解释器执行，与 ADR-0051 D4 平台脚本族同一语义）。
5. **族归类判据随之修正**（本单登记当场被 `tool-manifest` 门禁拦红暴露）：`python: null`
   自本单起有二义（平台族 / 无解释器外部族），`check_script_packages.py` 的族归类从
   「任一条目 python=null ⇒ 平台族」改为**树集驱动**：有树必登记必等价；无树条目豁免
   （外部族或整族删除）。**平台族整树删除未退役的门禁保护降级**为 PR 评审 +
   ADR-0051 D5（删除走人工 PR + 只读证据）；Phase 5 若需恢复机器判据，正解是给 manifest
   加族 kind 字段（schema 演进，需裁决——C5 六元组本单未动）。

## Alternatives

- **本 PR 直接 fleet 切 on**：弃——两族原用 `/usr/bin/python3` 跑，包面对象是 Agent venv 解释器，
  依赖面需 canary 真机验证（发布物已就位，切换只是设两个源键）；写进 `.env.example` 注释。
- **resolver 塞进 scan_runner 专属键**：弃——#3075 时一族一入口是权宜；泛化键让每个族
  独立回退，删除 `STP_UNISOC_*` 路径键（Phase 4b，走 ADR-0042 v1.3 豁免）的前置就是每族有替代通道。
- **把 `log` 排除写成 Start-Log-Scan 专属**：弃——与 `logs` 同语义（就地运行留下的产物），
  进全局排除面。

## Verification

- `env -i … pytest backend/agent/tests`（全套）→ **2123 passed**；
  `backend/agent/tests/test_tool_cache.py` 新增泛化 resolver 两例（python=null → `sys.executable`；
  族间键隔离）+ `test_unisoc_scan_runner.py` `TestPackagePlane` 四例（包面替换/显式优先/回退/两槽独立）
  + `backend/tests/services/test_agent_env_sync.py` 新键两例 → 62 passed；
- 两族 dry-run/登记/发布实跑：`tool_manifest.json` 38 族 213 条目、`check_tool_manifest --self-test`
  与 `--base origin/main` 绿；站点包源落 `Scan-Result-GT/2026.09.23.tar.gz`（40,836 B）与
  `Monkey-Log-Scan-GT-SPRD/2026.09.23.tar.gz`（39,537,104 B），副本 sha 一致；
- 打包器 `--python-absent` 首跑校验逻辑写坏（拒绝合法组合），修复后用「空键不推」双向测试自证；
- `tool-manifest` 门禁首跑即拦红（族二义）→ 归类判据改树集驱动 + 自测双向更新（无树条目豁免 / 有树必等价），复跑绿；
- `env_inventory --check` OK（263 名，4 新键登记进示例）；`check:quick` 15 gates OK；ruff OK。

## Revisit

- fleet 切换（运维）：canary 主机设 `STP_AGENT_UNISOC_*_PACKAGE_REF` → 重启推 env → 跑一轮
  Unisoc scan 验证 venv 解释器兼容 → 全量；之后 Phase 4b 删 `STP_UNISOC_{LOG,SCAN_RESULT}_{PYTHON,SCRIPT}`
  四键（ADR-0042 v1.3 豁免，同步 `.env*.example` / 清单 / 退役台账）。
- flashtool / aimonkey（resources/ 通道）不在本单：它们走 `host-resources` artifact 且有
  `STP_FLASH_TOOL_DIR` / `AIMONKEY_RESOURCE_DIR` 自己的解析链，入包要连脚本取值链一起改，另立单。
