# B9-U4：flash_firmware_unisoc_bsl@1.0.4 声明 spd_dump 工具依赖

Status: implemented
Class: feature

## Decision

依据 #4006 v1.3 §3.2 L1–L4 与 #4010 派单，把 `spd_dump` 纳入 ADR-0051 v1.7 D7 工具包机制，登记 `flash_firmware_unisoc_bsl@1.0.4`：

- **L1**：族树 `capabilities.json` 新增 `requires_tools: {"spd_dump": {"version": "20261113", "env": "STP_SPD_DUMP_DIR"}}`；版本取 `origin/main` 上 `tool_manifest.json` 中 `spd_dump` 族最新未退役条目（#4040，2026-11-18 合入）。
- **L2**：`resolve_spd_dump(route)` 只从 `STP_SPD_DUMP_DIR`（引擎注入的注册包根目录）取 `spd_dump`；删除 `STP_SPD_DUMP_PATH`、`PATH`（`shutil.which`）、`/usr/local/bin/spd_dump`、`/usr/bin/spd_dump` 三种回退（连同模块 docstring 的旧 env 契约一起更新）。未设置或目录下无可执行文件均抛 `FlashRejectedError`（带 `route`）。
- **L2 第 6 条（v1.2）**：工具路径解析从 `run()` 入口移到路由建立之后（`resolve_firmware_target` 返回 route 之后、`_load_flash_spec` 与前置闸门之前），因此「未设置环境变量」的拒绝结果带路由——1.0.3 在路由建立前解析，失败无路由。
- **L3**：`backend/schemas/pipeline_templates/flash_unisoc_bsl.json` 的 BSL 步骤 pin 由 1.0.3 改 1.0.4。
- **L4**：J1–J13 行为不变（锁、PROGRESS 戳形状、CLI、`spd_dump` 参数形状、成功判据均不改）。测试侧 28 处 `resolve_spd_dump` stub 同步接受可选 route 参数，断言不变；新增 3 条 U4 用例。
- **登记**：`check_script_packages.py --register flash_firmware_unisoc_bsl 1.0.4`，包 sha256 `08b9be956a322dc39da00026f9ae833fc0a95ea52704bb59d3269d4fa5ff4736`（2 文件 / 14106 bytes）。

涉及文件（均在 #4010 声明 scope 内）：`capabilities.json`、`flash_firmware_unisoc_bsl.py`、`test_flash_firmware_unisoc_bsl.py`、`flash_unisoc_bsl.json`、`tool_manifest.json`、本文档。

## Alternatives

- **保留 PATH / 固定路径回退**：否决，违反 #4006 F8 与 ADR-0051 D7 fail-closed——主机同名键会让工具版本脱离脚本身份，无法核验。
- **在 `run()` 入口（路由建立前）解析工具并在失败时附空路由**：否决，U4 第 6 条要求拒绝结果带真实路由；因此把解析移到路由建立之后。
- **在本单补 `libusb-1.0` 进展锐前置包集**：否决，`flash_preflight` 族不在 scope 且 §6 禁止；结论进「观察」。

## Verification

- 红（未改的 1.0.3）：3 条新用例全部失败——`test_u4_1_6` 报 `DID NOT RAISE FlashRejectedError`（旧代码回退 PATH/固定路径成功，无路由）；`test_u4_2` 同；`test_u4_3` 报 `TypeError: resolve_spd_dump() takes 0 positional arguments`（无 route 参数）。
- 绿：`backend/agent/tests/test_flash_firmware_unisoc_bsl.py` **62 passed**（59 条 U2 回归 + 3 条 U4）。
- 通用门禁：`check_script_packages.py` OK（40 族等价）；`check_tool_manifest.py --base origin/main` OK（47 族 / 281 版本，append-only）；`check:quick` **16 gates OK**；`git diff --check` OK；`env -i … run_pytest.py backend/agent/tests/ -q` 3477 passed / 1 failed / 2 skipped（失败为 `test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails`，在未改动的 main 检出上同样失败——本机存在 dialout 组导致，环境性，与 U4 无关）；`run_pytest.py tests/ -q`（CI 对齐 ignores）2537 passed / 18 skipped。

## Revisit

- 发包与 scan 归 §5.4 激活 B：本单**不**带 `--packages-root`、**不** scan；任何人不得在工具包发布前 scan 注册 1.0.4。
- `ldd` 实证：`spd_dump` 动态依赖 `libusb-1.0.so.0` 与 `libudev.so.1`（本机路径 `/lib/x86_64-linux-gnu/`）；展锐前置包集（`flash_preflight.py:155`）只含 `fastboot`，本批不改——缺库主机将在 `spd_dump` 启动时失败，验证主机 `.82` 已具备（L2 实跑通过）；按 #4006 §8 跟踪。
- 机队 `/usr/local/bin/spd_dump` 双构建并存（`.82` 验证版 `f7b5939c…`，`.71`/`.72`/`.74` 等旧 `5fd6b73c…`）；§5.4 发包后由 `STP_SPD_DUMP_DIR` 注册包统一。
