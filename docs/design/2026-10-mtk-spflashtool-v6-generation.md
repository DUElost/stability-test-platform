# MTK 刷机接入 SP Flash Tool V6 代际（535 / MLD_NX2）

- 日期：2026-10-12
- 状态：**已实施**（随 `feat/nx2-spflashtool-v6` 合入；**合入不等于生效**，上线路径见 §5）
- 上游决策：[ADR-0029 v2](../adr/ADR-0029-project-taxonomy-and-param-layering.md)（执行差异归脚本路由）、
  [ADR-0051](../adr/ADR-0051-release-unit-and-content-addressing.md) v1.7 D7（脚本包声明工具依赖）、
  [ADR-0033](../adr/ADR-0033-tool-kit-ecosystem-integration.md) D0/D3（外部工具不入仓、包存储）
- 同族前作：[2026-08 HONOR 刷机指纹路由](./2026-08-honor-flash-firmware-routing.md)、
  [刷机 Runbook](../operations/honor-flash-runbook.md)
- 范围：MLD 族新增机型 **MLD_NX2**（项目 535 / 板级 P535AA / MT6835）。552（LX2/LX3）的
  V5 行为不在本文改动面内。

## 0. 结论摘要

535 的刷机包由 **SP Flash Tool V6** 生成并驱动：命令行入口物从「DA + scatter 两个文件」
变成「一个 `download_agent/flash.xml`」，可执行文件也从 `SP_Flash_Tool_V5/flash_tool`
换成 `SP_Flash_Tool_V6/SPFlashToolV6`。这不是多一个参数，而是**代际（generation）**差异。

本设计把代际定为**固件包的属性**，写在版本目录的 `manifest.json` 里；刷机脚本
（`flash_firmware` / `flash_preflight`）按代际选工具根、拼命令行、校验入口物，
两代共用同一条执行链（指纹路由、host 锁、门控、重试、版本核验、boot 稳定）。
工具侧按 ADR-0051 D7 新增 `kind=tool` 族 `flashtool_v6@1.2444.00.100`，由脚本包
`requires_tools` 声明、引擎整包核验后注入包根。

## 1. 两代工具的实测差异

判据来源：本机 `flashtool@1.2444.00.100` 包内两代二进制各跑 `-h`（2026-10-12），
以及 535 现场整包升级成功日志（`260928V9`）。

| 面 | V5（552 在用） | V6（535） |
|---|---|---|
| 可执行 | `SP_Flash_Tool_V5/flash_tool` | `SP_Flash_Tool_V6/SPFlashToolV6` |
| 命令 | `-c firmware-upgrade -d <DA.bin> -s <scatter.txt> -b auto` | `-c firmware-upgrade -f <download_agent/flash.xml> -b auto` |
| 下载入口物 | DA 与 scatter 由命令行给 | `flash.xml` 内 `<dagent>` + `<scatter>`，按 flash.xml 所在目录相对解析（实测 `DA_BR.bin` + `../MT6835_Android_scatter.xml`） |
| scatter 形态 | `MT6768_Android_scatter.txt` | `MT6835_Android_scatter.xml`（V6 用 XML scatter） |
| `-b` | `--reboot`（布尔） | `--reboot`（布尔） |
| 结束标志 | `All commands are executed successfully` | `All command exec done!` |
| 运行库 | Qt4 X 面：`libICE/libSM/libXrender/libfontconfig/libglib` | Qt5 在包内 `lib/`；系统侧多出 GL 面（`libGL/libGLX/libGLdispatch`）与 KRB5 面（`libgssapi_krb5` 及依赖） |

`-b` 一行是勘误：两代都把 `-b` 定义为 `--reboot` 布尔，`auto` 是**被工具忽略的位置参数**。
552 生产一直以此形态运行且刷机有效，本版**不改**任何一侧的拼接，沿用现场已验证的
手工命令形态（把参数名 `boot_mode` 改成真实语义属行为中性重构，另单处理）。

## 2. 决策

- **D1 代际归固件包**：`manifest.json` 增 `tool_generation`（`"v5"` / `"v6"`）。缺省 v5——
  552 在用的全部版本目录没有这个键，缺省判 v5 即与 1.3.18 行为逐字一致。实施者不得
  把未知取值回落成 v5：`tool_generation` 是闭集，未知值必须 fail-fast（回落会拼出一条
  工具直接报错的命令行，失败点后移到刷机窗口里）。
- **D2 入口物判据按代际分流**：v5 的 manifest 必须给 `scatter_file` + `da_file`；
  v6 必须给 `flash_xml_file`。脚本只校验入口物**存在**，不校验 flash.xml 内部引用的
  DA/scatter 是否齐备——那是工具的 XML schema 校验职责（现场日志
  「XML schema validation passed!」），脚本重复一遍只会挪动失败点、不改变结果。
- **D3 新增 `kind=tool` 族 `flashtool_v6@1.2444.00.100`**，包根 = `SP_Flash_Tool_V6/` 的
  内容（入口 `SPFlashToolV6`），注入键 `STP_FLASH_TOOL_V6_DIR`；`flashtool@1.2444.00.100`
  条目**一字不动**。脚本回退链保留「selector 原样解压布局」（V5 根的兄弟目录），
  使资源目录/中心存储过渡形态与包形态同一判据。
- **D4 机型路由**：`MLD_NX2` / `MLD-NX2` → 族目录 `MLD`，版本靠 `latest.json` 的
  per-model 映射（1.3.6 能力）分流；代际由该版本的 manifest 决定。同族不同代际并存，
  不新建族目录——`flash_preflight` 的 `nfs-firmware-pointer` 项硬编码读 `MLD/latest.json`，
  新族目录会让这项检查失明。
- **D5 V6 运行库归 provisioning**：`flash_preflight` 增两项——`flashtool-v6`（硬门：
  V6 工具根在位却找不到入口即判失败）与 `qt-libs-v6`（**warning-only**）。V6 包做成硬门
  会让只刷 552 的主机被无关包名阻断；真正跑 v6 时 `flash_firmware` 的 `shared-libs`
  预检会硬失败并列出缺库名。provisioning 两面（`install_agent.sh`、
  `agent_flash_prereq_packages_v6`）与 preflight 常量由守卫逐项一致（#2133 同源守卫）。
- **D6 代际在指纹路由下只认 manifest**：路由模式不接受 `tool_generation` 参数覆盖
  （代际属于固件包，不接受调用方按机型传参——ADR-0029 v2 与新脚本评审 checklist）。
  `tool_generation` 参数只在显式 `firmware_dir` 分支作救急档（现场先拿到没有 manifest
  的 V6 包时不必等运维补文件）。

## 3. 放弃的备选

| 备选 | 放弃原因 |
|---|---|
| 把 V5+V6 合并成一个新 flashtool 包 | 已发布包不可改（ADR-0051 D1），合并=新 sha 让所有刷机主机重下 ~160MB；V5 工具身份无谓漂移；两代并非总要同时在场 |
| 新建脚本族 `flash_firmware_v6` | 2000 行执行链（锁/门控/重试/核验/boot 稳定）整份复制，正是 ADR-0033 D0 与 #735 要拦的形态 |
| 由 Plan 参数选代际 | 违反 ADR-0029 v2「执行差异归脚本路由」；5 组在用刷机计划都要按机型加分支 |
| V6 直接从中心存储 `tools/` 或 CIFS 读路径 | 绕开 D7 的内容寻址与整包核验，回到「主机 env 路径黑盒」（#3288 第 4 片刚删掉这类键） |
| 顺手把 `boot_mode` 改名/改成裸 `-b` | 行为中性但对 552 生产是纯风险，且不属于本 Requirement |

## 4. 参数与留痕面

- 新增 step 参数：`tool_generation`、`flash_xml_file`、`flash_tool_v6_dir`（均有
  [参数说明登记](../../backend/schemas/param_docs/flash_firmware.json)，版本范围 ≥1.3.19）。
- `metrics.route` 增 `tool_generation` / `flash_xml_file`；顶层 metrics 增
  `tool_generation`，v6 时 `da_file`/`scatter_file` 为 `null`（键位保留，step_trace
  消费面不塌陷）。命令行仍在 `metrics.command_argv` 里逐字留痕。
- 解析不到入口物/工具根的错误信息必须**指名代际与键名**（`flash_tool_v6_dir not found`），
  否则运维会去查 V5 目录。

## 5. 上线路径（运维，本 PR 不执行）

1. 发布工具包（源 = 中心存储 selector 解压物里的 `SP_Flash_Tool_V6`）：
   ```bash
   python tools/dev/package_tool_asset.py \
     --src <中心存储>/SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100/SP_Flash_Tool_V6 \
     --name flashtool_v6 --version 1.2444.00.100 --kind tool --python-absent \
     --script-relative SPFlashToolV6 --write-manifest \
     --packages-root <STP_AEE_NFS_ROOT>/packages
   ```
   预期：站点 `packages/flashtool_v6/1.2444.00.100.tar.gz` 的 sha == Git 登记
   `3e3bc805a6376a989a3e3113b92ae259549bcef45dd219a556a5c2b72181e53e`（49 文件 /
   55,677,853 字节；确定性打包器同树复跑同 sha）。
   **顺序要求**：先发工具包，再 scan 注册 / 重指脚本版本。`flash_firmware@1.3.19` 与
   `flash_preflight@1.0.7` 都声明了 `flashtool_v6`，包未就位时这些版本**所有**刷机步骤
   （含 552）在派发前 exit 2——这是 D7 的 fail-closed，不是可跳过的告警。
2. 发布脚本包 + `POST /api/v1/scripts/scan`（`created` 命中、`conflicts=0`）。
3. NX2 固件上架与 Plan 建立/重指，见 [刷机 Runbook §5](../operations/honor-flash-runbook.md)。
4. 真机取证（下表四项，任一与本文不符即回到代码侧修版本，不得靠改文档放行）。

## 6. 未验证项与风险

| 项 | 现状 | 取证方式 |
|---|---|---|
| NX2 的 `ro.product.model` 字面值 | 路由表按 `MLD_NX2`/`MLD-NX2` 双拼写登记（同 LX2/LX3 的历史教训）；现场若返回第三种写法，本步 fail-fast 并列出已知集合 | 单台 `adb shell getprop ro.product.model` |
| 目标版本的 `ro.build.version.incremental` | 决定版本目录名 / `latest.json` 值 / `manifest.version` 三处一致；上传包目录名带 `_FLASH` 后缀，未必等于设备读数 | 单台 `adb shell getprop ro.build.version.incremental` |
| Linux 侧 `-b auto` 位置参数 | Windows 侧同代工具实测可用；Linux 构建若判非法参数，改传裸 `-b` 即可（一个脚本版本的面） | 单台真机刷机 |
| NX2 的 DA 态 USB pid | 门控压制集合 `_FLASH_STAGE_PIDS` 取自在用 MTK 机型实测（0003/2000/2001/201c/2026/3000）；V6 日志只见到 PreLoader `2000`。若 NX2 的 DA 态 pid 不在集合内，同 host 邻机的刷机态口不会被隐藏，可能重演抓错设备 | 刷机窗口内 `lsusb` 采样同 host 全部 `0e8d` pid |
| 首次刷机耗时与 `timeout_seconds`（默认 1200s） | V6 日志显示 `super` 分区 4.67G；552 的同名分区实测在 1200s 内完成，NX2 未测 | 首台观察 `step_trace` 的 percent 戳与 exit_code |
