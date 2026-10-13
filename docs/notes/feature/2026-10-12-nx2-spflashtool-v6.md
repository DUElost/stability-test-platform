# 535 / MLD_NX2 接入 SP Flash Tool V6 代际（flash_firmware 1.3.19 / flash_preflight 1.0.7）

Status: implemented
Class: feature

> 本 Note 的 implemented 只覆盖**仓库侧**。工具包未发布、脚本包未 publish、未 scan、
> 无 NX2 真机验证——生效判据见 Revisit 与 Runbook §7。

## Decision

MLD_NX2（项目 535，板级 P535AA / MT6835）的刷机包由 **SP Flash Tool V6** 驱动，与在用 552
（LX2/LX3，V5）在两处不同：可执行文件 `SP_Flash_Tool_V6/SPFlashToolV6`，入口物从
`-d <DA> -s <scatter>` 变成单一 `-f <download_agent/flash.xml>`（DA 与 scatter 由 flash.xml
内部引用，按 XML 所在目录相对解析）。本单把「代际」定为**固件包的属性**：

1. `manifest.json` 增 `tool_generation`（闭集 `v5`/`v6`，缺省 `v5`）。552 在用的全部版本目录
   没有这个键 → 缺省判 v5 即与 1.3.18 逐字等价；未知取值 fail-fast，不回落 v5（回落会拼出
   一条工具直接报错的命令行，失败点后移到刷机窗口）。
2. 入口物判据按代际分流：v5 必须给 `scatter_file`+`da_file`；v6 必须给 `flash_xml_file`。
   v6 时 route 的 `da_file`/`scatter_file` 为 `null`，顶层 metrics 键位保留（step_trace 消费面
   不塌陷）。脚本只校验 flash.xml 存在，不校验其内部引用的 DA/scatter——工具自己做
   XML schema 校验，脚本重复一遍只挪动失败点、不改变结果。
3. 命令行拼接抽成 `_build_flash_argv()`（纯函数，可直接断言两代形态），V5 分支与 1.3.18
   **逐字一致**；`main()` 的 argv 仍写进 `metrics.command_argv`。
4. 工具侧按 ADR-0051 v1.7 D7 新增 `kind=tool` 族 `flashtool_v6@1.2444.00.100`（包根 =
   `SP_Flash_Tool_V6/` 内容，入口 `SPFlashToolV6`，注入键 `STP_FLASH_TOOL_V6_DIR`），
   `flashtool@1.2444.00.100` 条目一字不动；两族都写进两个脚本族的 `capabilities.json`。
5. 路由 `MLD_NX2`/`MLD-NX2` → 既有族目录 `MLD`，版本靠 `latest.json` 的 per-model 映射分流；
   不新建族目录——`flash_preflight` 的 `nfs-firmware-pointer` 项硬编码读 `MLD/latest.json`，
   新族目录会让这项检查失明。指纹路由下一代际**只认 manifest**，不接受 `tool_generation`
   参数覆盖（ADR-0029 v2「执行差异归脚本路由」）。
6. `flash_preflight@1.0.7` 增两项：`flashtool-v6`（硬门，V6 工具根在位却找不到入口即失败；
   一个根都没有时记 `skipped` 不判失败——包未发布/未拉到时本步根本不会启动）与
   `v6-shared-libs`（**warning-only**）。V6 包做成硬门会让只刷 552 的主机被无关包名阻断；真正跑
   v6 时 `flash_firmware` 的 `shared-libs` 预检会硬失败并列出缺库名。既有六项条目名与判据不变。
7. `v6-shared-libs` 在非 dpkg 系统只能说「无法判定」（`non-dpkg system, skipped:`），
   不得写成「V6 运行库齐备」——与 `qt-libs` 同判据，避免 warning-only 项造假绿。
8. provisioning 补 V6 运行库（`ldd SPFlashToolV6` 相对 V5 多出的 GL 面与 KRB5 面，Qt5 本体在
   包内 `lib/`）：`agent_flash_prereq_packages_v6` + `install_agent.sh` 同一循环；#2133 同源
   守卫扩到 V6 那一组（四面逐项一致，防止「preflight 检查的」与「安装链装的」再分叉）。
9. `-b` 语义勘误入文档：两代 `-h` 都把 `-b` 定义为 `--reboot` 布尔，`auto` 是被工具忽略的
   位置参数；552 生产一直以此形态运行且刷机有效，本版不改任何一侧拼接（改名属行为中性
   重构，另单）。

设计口径与上线路径：[`docs/design/2026-10-mtk-spflashtool-v6-generation.md`](../../design/2026-10-mtk-spflashtool-v6-generation.md)；
操作面：[刷机 Runbook §7](../../operations/honor-flash-runbook.md)。

## Alternatives

- **V5+V6 合并成一个新 flashtool 包**：弃——已发布包不可改（ADR-0051 D1），合并=新 sha 让每台
  刷机主机重下 ~160MB，V5 工具身份无谓漂移，且两代并非总要同时在场。
- **新建脚本族 `flash_firmware_v6`**：弃——2000 行执行链（host 锁 / 门控 / 重试 / 版本核验 /
  boot 稳定）整份复制，正是 ADR-0033 D0 与 #735 要拦的形态。
- **由 Plan 参数选代际**：弃——违反 ADR-0029 v2；5 组在用刷机计划都要按机型加分支。
- **V6 直接读中心存储 `tools/` 或 CIFS 路径**：弃——绕开 D7 的内容寻址与整包核验，回到
  主机 env 路径黑盒（#3288 第 4 片刚删掉这类键）。
- **`v6-shared-libs` 做成硬门**：弃——552-only 主机被无关包名阻断，见 Decision 第 6 条。
- **顺手把 `boot_mode` 改成裸 `-b`**：弃——对 552 生产是纯风险且不属于本 Requirement。

## Verification

静态/确定性（本机，2026-10-12）：

- 代际判据实测：`SPFlashToolV6 -h` 给出 `-f, --flash_xml` / `-c … firmware-upgrade` /
  `-b, --reboot`；V5 `flash_tool -h` 给出 `-s, --scatter` / `-d, --da_agent` / `-b, --reboot`。
  `ldd` 两代对比得 V6 独占依赖 = `libGL/libGLX/libGLdispatch` + `libgssapi_krb5/libkrb5/
  libk5crypto/libcom_err/libkeyutils/libresolv`；V5 独占 = Qt4 X 面（libICE/libSM/libXrender/
  libfontconfig/libglib/…）——两代不互相替代，故 preflight 是「加一项」而不是「换包名」。
- 入口物事实核对：`/data/apk-repo/incoming/firmware/P535AA_EVT_16.0_NX2_USERROOT_260928V9_FLASH/
  download_agent/` 含 `flash.xml`（`<dagent>DA_BR.bin</dagent>` +
  `<scatter>../MT6835_Android_scatter.xml</scatter>`）与 `flash.xsd`；该目录**没有** manifest.json
  （上架时才补，见 Runbook §7）。
- `package_tool_asset.py … --dry-run` 复跑两次同 sha
  `3e3bc805a6376a989a3e3113b92ae259549bcef45dd219a556a5c2b72181e53e`（49 文件 / 55,677,853 字节）
  → 确定性打包成立，登记 sha 与后续发布字节可核。
- `check_script_packages --register flash_firmware 1.3.19` / `--register flash_preflight 1.0.7`
  通过；`check_script_packages` 35 族等价；`check_tool_manifest` 40 族 / 243 条相对
  `origin/main` append-only 绿；`check_resource_anchors` 绿（新增 A27/A28 与 `flashtool_v6`
  绑定声明，28 锚，防扩张通过）；`env_inventory --check` 271 读取名一致。
- 测试（均经 `scripts/run_pytest.py` 的 cgroup 硬顶入口）：
  `backend/agent/tests/test_flash_firmware_v1319.py` 31 passed；
  `backend/agent/tests/test_flash_preflight_v107.py`（判据改 ldd 后）12 passed；
  `-k flash_preflight` 47 passed、`-k flash` 202 passed；
  `backend/agent/tests -k "flash or required_tools or tool_cache or tool_asset or script_packages"`
  264 passed（含 v102–v105 preflight 套件与 v1310/v1311 main 流程套件）；
  **Agent 全套 `backend/agent/tests` 2551 passed**（105s，无失败）；
  **根 `tests/` 全量 2400 passed / 18 skipped**（11m07s，无失败）；
  `tests/test_flash_provisioning_prereqs_2133.py` 16 passed；
  `tests/test_script_seed_governance.py` + `tests/test_pipeline_template_script_pins_2865.py` +
  `backend/tests/test_pending_activation_view_2931.py` 14 passed；
  `test_param_docs_content.py` + `test_param_docs_registry.py` +
  `test_plan_parameter_projection.py` 129 passed / 1 skipped；
  `-m compileall backend/ tools/ scripts/ tests/` rc=0。
- `scripts/run_gates.py check:quick` → **16 gates OK**；`scripts/run_gates.py check:pr` →
  **23 gates OK**（rc=0；含 tool-manifest / resource-anchors / gov-surface / agent-tests /
  pr-migrate）。
- CI 复跑结论记在 PR 评论（不以「命令跑过」代替结果）。
- 为签名变化同步的既有桩：`test_flash_firmware_v1310/v1311` 的 `_pick_flash_tool_exe` 桩改为
  带默认值的两参 lambda；`test_flash_preflight_v102` 的条目名集合加
  `v6-shared-libs` / `flashtool-v6`（两项确为本版新增）。
- CI 首跑（PR #3689 `pr-agent-tests`）红在 **census 计数钉**：`tests/test_check_resource_anchors.py`
  把声明面钉成 26 锚 / `A01..A26`，本单新增 A27/A28 后真实输出是 28 锚。已同步该守卫
  （28 锚 / `A01..A28`，类 docstring 写明「新增锚须在此同步计数；`--base` 的防扩张面抓的是把
  既有候选改判，不是新增候选的显式声明」），本地该文件 37 passed。
  **漏检根因：首跑只跑了选中的根文件，没跑根 `tests/` 全量**——CI 的根套件才是覆盖面。

真机**只读**取证（2026-10-12，控制面 + 一台 NX2 host（`172.21.x.x`，末段 `.83`），
设备 `ATHTNX69****0085`；**未刷任何设备**）：

- ① `ro.product.model` = **`MLD-NX2`**；平台设备表记作 `MLD_NX2`（取自 `adb devices`）——
  两个拼写都已在 `_MODEL_FAMILY_ROUTES` 里。用真机读数跑 1.3.19 的指纹路由：
  `family=MLD / version=P535AA-HONOR-NX2-16-260928V9 / tool_generation=v6`，
  argv = `[SPFlashToolV6, -c, firmware-upgrade, -f, <…>/download_agent/flash.xml, -b, auto]`
  （不含 `-d/-s`），da/scatter 为 `null`。
- ② `ro.build.version.incremental` = **`P535AA-HONOR-NX2-16-260928V9`**，而
  `ro.build.display.id` = `MLD-NX2-16-260928V9`——**两者不同形**。上架目录名与
  `manifest.version` 一律取 incremental；照抄界面/文件名会让 `verify_version` 永远判不一致。
- ③ Linux 侧 `-b auto` **被接受**（不再是 Windows-only 证据）：Debian 13 上
  `SPFlashToolV6 -c firmware-upgrade -f <真包 flash.xml> -b auto` 先打
  `Command line arguments parse pass!`，再两次 `XML schema validation passed!`，随后
  `scan device START, timeout set as 120000 ms`；无设备在场时以
  `scan device com port failed, error code: -1` + rc=1 收场 → 既有 PASS 判据不命中即判失败，
  语义正确（工具版本横幅：`MediaTek Smart Phone Flash Tool V6 v6.2444.00`）。
- 阻断项（新发现）：NX2 四台 host（`172.21.x.x`，末段 `.83–.86`，Debian 13）**没有 V6 运行库**——
  `ldd SPFlashToolV6` 报 `libGL.so.1 => not found`，连 `-h` 都起不来（控制面装了
  `libgl1/libglx0/libglvnd0` 才可跑）；KRB5 面四台已具备。真机刷 V6 前必须按
  `agent_flash_prereq_packages_v6` 补包；不补时刷机步会在拿锁之前以
  `environment precheck failed: shared-libs: missing: libGL.so.1` 硬失败。
- 判据问题（实测暴露，另立 #3690）：初始门控集合含 `201c`，而 `0e8d:201c` 实为
  MLD-LX2/LX3/NX2 的**正常 adb 在线态**（`.63` 23 台全 201c 且 adb 可见；`.85` 另有 1 台 2046）
  → 在混跑 host 上刷一台会把同 host 邻机一起 `authorized=0`。v1.3.x 既有判据问题，不是 V6
  引入，但 535 批次把刷机与开关机/Monkey 放同批 host 会放大它。本单只记录，不改判据。
- 新脚本在真机上的**只读执行核验**（把族树文件与已发布工具包放到 host 的 `/tmp` 下，
  `fix=false` 跑 `flash_preflight@1.0.7`；两台机器各证一条分支）：
  - 缺库那台（NX2 host，`172.21.x.x` 末段 `.83`）：`v6-shared-libs` =
    `missing libs: libGL.so.1 —— 补包（provisioning）：libgl1`，`flashtool-v6` = ok，
    `nfs-firmware-pointer` = ok（证明我上架的 `latest.json` 对 Agent 用户可读），整步 success=True；
  - 有库那台（控制面 Debian 13）：`v6-shared-libs` = `ldd 无缺库（…/SPFlashToolV6）`，
    `udev-rule`/`dialout-group` 按本机实况判 False（这台不是刷机 host）——
    说明 warning-only 与硬门两条路径都按判据走，没把「没测」写成「齐备」。
  - **本轮实跑抓到自己写的假绿**：第一版 `v6-shared-libs` 用 dpkg 包名判定，而这台
    host 上 `libgl1` 从未安装 → `dpkg-query` 返回「无法判定」，代码把「无从判定」渲染成
    `non-dpkg system, skipped`（措辞也错，那是 dpkg 系统）。已改为**对入口跑 `ldd`**
    （与刷机步同一 `LD_LIBRARY_PATH` 口径），缺失 soname 才报缺失，并按
    `_V6_PACKAGE_HINTS` 指回包名；新增 `test_dpkg_blindness_never_becomes_false_ready`
    与 `_ldd_missing_libs` 的两条形态用例（缺库→warning+包名、无法探测→空表不判失败）。
- D7 两工具声明的**真包拉取核验**（站点包源只读、缓存写临时目录）：
  `inject_required_tools(flash_firmware 族树, env)` → 返回 None；
  `STP_FLASH_TOOL_DIR` → `…/flashtool/1.2444.00.100`（根内 `flash_tool`），
  `STP_FLASH_TOOL_V6_DIR` → `…/flashtool_v6/1.2444.00.100`（根内 `SPFlashToolV6`），
  两个根各自带 `.stp-verified` → 「一版脚本依赖两代工具包」在拉包/核验/注入面成立。
- 现网设备状态核对（只读）：NX2 共 50 台，45 台被 run 906（plan 78「MLD_NX2-开关机专项-18H」，
  18:09 起、timeout 64800s）以 `active_lease` 占住，4 台 `adb_excluded`（离线）、1 台不在该 run 且
  OFFLINE → **此刻没有可用于破坏性刷新的空闲 NX2 设备**。
- 反例核对（只读，用**站点在用包 1.3.18** 的字节）：面对 v6 目录两条路都拒绝——
  `no firmware family route for model MLD-NX2; known models: …`（不含 NX2）与
  `manifest.json must define scatter_file and da_file` → 固件先上架不会让 552 计划在用版本
  误刷 NX2 包。
- ④⑤（接管态 USB pid、`super` 4.67G 首刷耗时）已在下面的真机刷机里取到，不再列为未知。

真机 V6 成功刷机一次（Owner 授权：先停 `.85` 的 13 个开关机作业并清环境）：

- 停线：13 个 job（12 RUNNING + 1 已 ABORTED）经 `manual-exit` 收口为 ABORTED；
  **实测 ADR-0022 BO4 的缺口**——abort 后 12 台的 `boot_count` 仍从 98 涨到 99/109
  （设备侧 `PowerCycleService` 在自动重启），于是用平台自有收尾步建 Plan 79
  （`powercycle_finish@1.0.8`，`project=chain`）派发 run 911，12/12 COMPLETED；
  之后两次采样 `boot_count` 完全冻结、`svc=0`、主机无残留进程 → 环境才算清干净。
  （934/`0226` 与 `0172` 当时 adb 不可达，收尾步下发不了，已记录为现场待确认项。）
- 刷机：`1.3.19` 的**已登记包字节**（sha 一致）在 `.85` 上手动执行（PR 未合入 →
  控制面部署树里没有该版本，scan 无从注册，这是结构限制不是选择）。
  结果 `success=true / exit_code=0 / duration 357.6s`，
  argv `SPFlashToolV6 -c firmware-upgrade -f <staged>/download_agent/flash.xml -b auto`，
  目标口 pid 轨迹 `201c→2000→201c`（**接管态在既有集合内，没有新 pid**），
  版本回读与 `manifest.version` 一致。
- 由这次实跑修掉 `caught_brom` 的 V5-only 措辞判据（真机留下 `caught_brom=false` 的假诊断）；
  并留下 #3690 的量化证据（门控隐藏 11 个 `201c` 正常态邻机口、`regate=22`）。

站点侧实际写操作（本单做过，全部只增不改）：

- 发布 `packages/flashtool_v6/1.2444.00.100.tar.gz`（55,677,853 字节；发布守卫核对
  sha == Git 登记 `3e3bc805a637…`；复跑幂等「站点已有同字节包」）。派生副本
  `packages/manifest.json` 重写为「`origin/main` 登记面 + `flashtool_v6`」——**不含**
  尚未发布的 `flash_firmware@1.3.19` / `flash_preflight@1.0.7`，避免站点 advertise 不存在的
  字节；核对：副本里每个最新未退役条目都能在站点找到包。
- 上架 NX2 固件 `firmware/MLD/P535AA-HONOR-NX2-16-260928V9/`（59 文件 / 6.2G，与
  `apk-incoming` 源同量；`super.img`/`boot.img`/`flash.xml`/`DA_BR.bin`/scatter 逐项等字节，
  `super.img` sha256 前缀一致）+ v6 `manifest.json`；`MLD/latest.json` 新增 `MLD_NX2` 键并
  保留 `MLD_LX2`/`MLD_LX3`（原文件备份在会话产物里）。**踩坑**：第一次用
  `tempfile.NamedTemporaryFile` 落盘得到 0600，CIFS 上 Agent 用户读不到指针 → 改为
  显式 `chmod 0644` + `os.replace`（Runbook §7 第 5 步已写死这条）。
- **当时没有**：`--publish` 脚本包、`POST /scripts/scan`、建/重指 Plan、刷任何设备、改中心存储
  既有目录。（当时控制面部署树的 `tool_manifest.json` 里没有 1.3.19/1.0.7，scan 结构上看不见
  它们 → 合入 + 控制面发布是真机链路的前置；顺序红线：`flashtool_v6` 包必须先于 scan 就位。）

合入之后（2026-10-13，Owner 授权「合入 / 重挂 18H / 三台待现场排查」之后做的事）：

- `--publish`：落 `flash_firmware@1.3.19`、`flash_preflight@1.0.7`（其余 33 族同字节跳过）；
  三包逐包 `sha256sum` == Git 登记，派生副本 `packages/manifest.json` 随发布同步；
- 控制面发布：`build_bundle.py` 出 rev `80c446fc`（`schema_target=b2c3d4e5f6a7` 与库内 head
  相同 → 无迁移）；新 rev `venv/` 按 `--require-hashes -r backend/requirements.lock` 装，
  归一化比对 **lock=77 缺装=无 版本不一致=无**（多的只有 `pip` 种子包）；`.env.backend` 指
  树内 `../env.backend`，`logs/`、`tools/ansible/inventory.ini` 到位；`current` 原子切换 +
  `systemctl restart stability-backend` → `/health` healthy / unit `active`（回滚位 `fbb5ec61`）；
- `POST /api/v1/scripts/scan`：**created=2、conflicts=0**，`package_missing` 不含本次三包；
  库内 `flash_firmware@1.3.19` / `flash_preflight@1.0.7` 均 `is_active=true`；参数元数据与
  在用 1.3.15/1.3.18 同形（scan 建行为空 schema——本族一直靠 `default_params`/param_docs
  承载，不在本单改这个形态）；
- 四台 NX2 host 各一轮 `POST /script-presence/refresh`：**missing=0 / mismatch=0**
  （present 17–22，其余 n/a）→ 新脚本版本与工具包在这些 host 上可核验拉取；
- 重挂 18H：run 920（plan 78，11 台 ONLINE 设备）init 11/11 → patrol，抽验 `…0138`
  `boot_count 92→94`、`ServiceRecord=1` → 循环确实恢复；排除待现场的三台
  （`…0235`/928、`…0226`/934、`…0172`）。

仍未做（各有理由，别记成已完成）：

- **552 在用刷机计划没有重指**：`--plan-step-drift` 仍列 plan 39/40/59/61/62/68 等
  `flash_firmware 1.3.18 → 1.3.19`、`flash_preflight 1.0.6 → 1.0.7`。重指会让这些 host 也去拉
  `flashtool_v6`（+55.7MB 下载 / +139MB 盘）——「一版脚本同时声明两代工具」的代价；
  是否追平归 Owner，追平前按 #3030 过参数/预算契约核对。
- **平台侧 V6 首刷 canary（刷机 → `oobe_skip` → `ensure_root` 紧跟）仍未跑**：
  928 停在 `0e8d:2046` 无 adb，需现场恢复后才能验「首刷后设备能否回到可测态」。
- `--pending-activation` 把 `flashtool` / `flashtool_v6` / `aimonkey` 记为 `unregistered`：
  该视图按 `script` 行对账，`kind=tool` 族本就没有 script 行（V5 的 `flashtool` 同样被列出），
  属既有口径，不在本单顺手改判据。

## Revisit

- 发布链落地后（Runbook §7）：`--pending-activation` 与 `--plan-step-drift` 两个视图清空才算
  「新版本已生效」，不以合入或 scan 为准。
- `boot_mode` → 真实语义（`-b/--reboot`）的行为中性改名，连同 #3512（SP Flash Tool 对缺失
  `.sig` 的良性 `FAIL` 字样被误判为刷机失败）一并处理；V6 日志是否含同类良性 FAIL 需在
  真机窗口取证。
- `apk-incoming/firmware` 已有 `P535AA_EVT_16.0_NX3…` 与 `NX3Carrier…` 两包：到位时若 model 串
  落在现有路由键之外，需发新版本补键（参数面已有显式 `firmware_dir` + `tool_generation` 逃生档）。
- 552 若整体迁 V6：`flashtool@1.2444.00.100` 退役要先确认没有 active 脚本版本仍声明它
  （ADR-0051 D7 门禁判据），退役面按 ADR-0051 D5 / #735。
- #3690（门控把正常态 `201c` 当刷机态）裁决后，`_FLASH_STAGE_PIDS` 可能按 model/family
  参数化——那会再动一次 `flash_firmware` 的门控判据，需要独立真机窗口取证。
- `apk-incoming` 里的 `P535AA_EVT_16.0_NX3…` / `NX3Carrier…` 两包同为 V6 形态：若其 model 落在
  `MLD_NX3` 一类新串，需发版本补路由键（显式 `firmware_dir` + `tool_generation` 参数档可先救急）。
- NX2 host 的 `libgl1/libglx0/libglvnd0` 补齐后，`v6-shared-libs` 应从 warning 转回正常判定；
  若 fleet 里出现「装了 GL 却仍被别项阻断」，回到本单取证方法（两代 `ldd` 差集）复核。
- 若出现「同一 host 并刷 V5 与 V6 机型」的场景，host 级 flock 仍保证每 host 同时 1 台，
  但门控 pid 集合需按机型合并——现在是一份全局集合，无代际维度。
