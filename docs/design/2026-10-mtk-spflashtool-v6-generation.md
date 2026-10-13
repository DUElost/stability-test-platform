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
- **D5 V6 运行库归 provisioning，且 preflight 的判据是 `ldd` 而不是 dpkg 包名**：
  未安装过的包名在 dpkg 数据库里查不到（只能返回「无法判定」），按包名判定会把
  「缺库」渲染成「齐备/跳过」——2026-10-12 在 NX2 host 上实跑 `flash_preflight@1.0.7`
  正是这样暴露的（那台机器 `libGL.so.1` 确实缺失）。改成对入口跑 `ldd`（与刷机步
  同一 `LD_LIBRARY_PATH`）后，报的是缺哪个 soname、并指回应补的包名。
`flash_preflight` 增两项——`flashtool-v6`（硬门：
  V6 工具根在位却找不到入口即判失败）与 `v6-shared-libs`（**warning-only**）。V6 包做成硬门
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

## 6. 取证结果与遗留风险

2026-10-12 取证（只读，控制面 + 一台 NX2 host（`172.21.x.x`，末段 `.83`），
设备 `ATHTNX69****0085`）：

| 项 | 结论 | 依据 |
|---|---|---|
| ① NX2 的 `ro.product.model` | **`MLD-NX2`**（连字符）；平台设备表记为 `MLD_NX2`（下划线，来源 `adb devices`）——两个拼写都已在路由表 | `adb shell getprop ro.product.model` + `GET /api/v1/devices?project_key=P535AA` |
| ② 目标版本口径 | `ro.build.version.incremental` = **`P535AA-HONOR-NX2-16-260928V9`**；`ro.build.display.id` = `MLD-NX2-16-260928V9`（≠ incremental，别照抄界面/目录名） | 同上；上架目录名与 `manifest.version` 均取 incremental 值 |
| ③ Linux 侧 V6 的 `-b auto` | **接受**：`Command line arguments parse pass!`，随后完成 `validating XML schema / XML schema validation passed!` 并进入 `scan device START, timeout set as 120000 ms`；无设备时以 `scan device com port failed, error code: -1` + rc=1 收场 | Debian 13 上对 `SP_Flash_Tool_V6 v6.2444.00`（Build Nov 4 2024）实跑 |
| ④ NX2 刷机态 USB pid | **已取证**：真机目标口轨迹 `201c → 0e8d:2000 → 201c`，接管态 `2000` **在既有集合内**（`_BROM_STAGE_PIDS` 也含它），未出现集合外的新 pid；正常态实测为 `201c`（`2046` 亦见于在线设备） | 刷机窗口内 1.5s 采样 sysfs（`172.21.x.x` 末段 `.85`） |
| ⑤ 首刷耗时 | **357.6s** 走完 `firmware-upgrade`（含 `super` 4.67G）+ 版本回读一致 → 默认 `timeout_seconds=1200` 够用 | `metrics.duration_seconds` / `attempts[0].verdict` |

**刷机成功后又观察到一件事（重要）**：设备在 `success=true`、版本回读一致之后约 10–30 分钟，
从「adb 可见」掉成 `0e8d:2046`——有 USB serial、**没有 adb 接口**，与该 host 上长期 OFFLINE 的
`…0172` / `…0226` 同形；非 root 无法写 `authorized` 复位。两条结论：
① V6 首刷的 Plan 链必须让 `oobe_skip` + `ensure_root` **紧跟**刷机步，别等 adb 窗口关掉；
② **`success=true` 不等于「设备回到可测状态」**，收口判据要再加一条平台侧
`ONLINE + adb_state=device` 复查（本次因无 Plan 载体没做到，故记在这里而不是记成完成）。

遗留风险（④⑤ 之外）：

- **NX2 host 目前跑不起 V6**：`172.21.x.x`（末段 `.83`）上 `ldd SPFlashToolV6` 报
  `libGL.so.1 => not found`，`SPFlashToolV6 -h` 直接 `error while loading shared
  libraries: libGL.so.1`。控制面（Debian 13，已装 `libgl1/libglx0/libglvnd0`）无此问题。
  → 刷机前必须按 §5 的 provisioning 面补包（`v6-shared-libs` 只给 warning，真正拦下它的是
  `flash_firmware` 的 `shared-libs` 预检——它会在拿锁之前硬失败并列出缺库名）。
- **中心存储上 V6 工具包已发布**：`packages/flashtool_v6/1.2444.00.100.tar.gz`
  （sha 与 Git 登记一致，发布守卫核验）；**NX2 固件已上架**
  `firmware/MLD/P535AA-HONOR-NX2-16-260928V9/`（59 文件 / 6.2G，与 `apk-incoming` 源逐项
  等量，`super.img` sha 前缀一致）+ v6 `manifest.json` + `latest.json` 增 `MLD_NX2` 键。
  在用 1.3.18 对该目录**两条路都拒绝**（无 NX2 路由；v6 manifest 无 scatter/da）——
  上架本身不会让 552 计划误刷 NX2 包。
- **脚本版本注册**：合入（`80c446fc`）→ `--publish` 两包 → 控制面发布 rev `80c446fc`
  → `POST /scripts/scan` **created=2 / conflicts=0**，两版本 `is_active=true`；
  四台 NX2 host `script-presence/refresh` 全部 missing=0 / mismatch=0。
  结构结论沿用：**scan 的可见性 = 部署树里的 `tool_manifest.json`**，所以「合入 + 控制面发布」
  是真机链路的硬前置，publish 只是把字节放到站点。
- `super` 分区 4.67G 的首刷耗时未测（默认 `timeout_seconds=1200`）。
- **门控判据的历史问题（本单实测暴露，另立 #3690 裁决）**：`_FLASH_STAGE_PIDS` 含
  `201c`，而 `201c` 实为 MLD-LX2/LX3/NX2 的**正常 adb 态**。v1.3.9 只把 `201c` 从
  「保持阶段」移除，**初始门控仍会把它隐藏** → 在同 host 还有其它测试在跑时，一次刷机会
  把邻机的 adb 一起摘掉（`authorized=0`，刷机结束才恢复）。这不是 V6 引入的，但 535 批次
  把刷机与 Monkey/开关机混在同一批 host 上时会被放大。
