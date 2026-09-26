# ADR-0051 D7：flashtool / aimonkey 以 kind=tool 登记 + 站点发布守卫（2026-09-26）

Status: implemented（Git 登记；站点发布为合入后运维步骤）
Class: feature

## Decision

#3288 第 2 片。D7 v1.7 绑定机制（#3365）需要有可声明的工具包：

1. **登记两条 `kind=tool` 条目**（`python: null`，`package_tool_asset --kind tool --python-absent --write-manifest`）：

   | 族@版本 | 包根 = 源目录内容 | 入口 `script` | sha256 | 成员 / 压缩后 |
   |---|---|---|---|---|
   | `flashtool@1.2444.00.100` | `resources/flashtool/SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100/` | `flash_tool` | `ffc30c93…d66` | 76 / 78.9MB |
   | `aimonkey@20260317` | `resources/aimonkey/AIMonkeyTest_20260317/` | `MonkeyTest.py` | `ea4b73a0…406` | 32 / 51.8MB |

   包根取**内层目录**：`STP_FLASH_TOOL_DIR` 的现行语义就是「含 `flash_tool` 的目录」（#3234 注入值、
   plan 68 显式参数同形）；`aimonkey_paths.resolve_aimonkey_bundle_dir` 在根下直接有 `MonkeyTest.py` 时
   即以根为 bundle——两键注入包根即与现行消费方零差异。源 = 控制面检出 `backend/agent/resources/`
   （gitignored，即 host-resources 通道一直在下发的那份字节）。
2. **站点发布守卫** `publish_rejection`：只带 `--packages-root` 的发布此前直接 `write_bytes`——
   不核对 Git 登记 sha、会覆写已发布包。现在：未登记 → 拒；本次打包 sha ≠ 登记 → 拒（源目录变了就该发新版本）；
   站点已有同名不同字节 → 拒（只增不改）；同字节 → 幂等不重写。

## Alternatives

- **包根取外层 `resources/flashtool/`**：入口成 `SP_Flash…/flash_tool`，注入值需再拼一层，消费方语义要改；弃。
- **aimonkey 以 `AIMONKEY_RESOURCE_DIR` 的「资源根」语义（bundle 的父目录）打包**：多一层无意义目录，
  且 `aimonkey_paths` 已支持「根即 bundle」；弃。
- **CRLF 归一后再打包**：aimonkey 有 13 个 CRLF 文件（全是 Python / 文本，无 shell——无执行风险）；
  41/48 台主机现跑的就是 CRLF 字节（#3128 记录的 7 台 LF 偏离正是这批文件）。归一会让包与现行主机
  内容不同、制造第二次变更；保留原字节，入包后 7 台偏离随 tools_cache 自然消失。

## Verification

- 确定性：两工具各 dry-run 两次 sha 相同；无软链、无特殊文件（agent 解包判据放行）；pyc/`__pycache__` 被排除面挡住
- `TestPublishGuard` 5 例：未登记 / sha 不符 / 覆写不同字节 均拒、同字节幂等；端到端「登记后源变 → 仅
  `--packages-root` 发布」返回 1 且站点零写入。**旧发布路径上 4 红**（桩放行后暴露 `write_bytes` 行为），修复后
  `test_adr0033_phase_b.py` 14 passed
- `check_tool_manifest --base origin/main`：40 族 / 217 条目 append-only 绿；`check_script_packages` 35 族绿
  （kind=tool 无树豁免）；`check:quick` 17 gates OK
- 站点现状核对：`/mnt/stp-aee/packages` 无 flashtool/aimonkey 包、站点 manifest 无此两族

## Revisit

- **合入后发布**（运维，站点 append-only 写入）：在控制面检出按同源同参跑两次
  `package_tool_asset … --write-manifest --packages-root /mnt/stp-aee/packages`（`--write-manifest` 在已登记时
  幂等，发布守卫再核一次 sha）；核对站点 tarball sha == 登记、`manifest.json` 副本含两族。
- 第 3 片：flash_firmware / flash_preflight / monkey 族新版本声明 `requires_tools`（依赖 #3365），真机刷机窗口取证，
  计划重指；之后第 4 片删 `STP_FLASH_TOOL_DIR` 注入与 host-resources 通道（#3356）。
