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
   `qt-libs-v6`（**warning-only**）。V6 包做成硬门会让只刷 552 的主机被无关包名阻断；真正跑
   v6 时 `flash_firmware` 的 `shared-libs` 预检会硬失败并列出缺库名。既有六项条目名与判据不变。
7. provisioning 补 V6 运行库（`ldd SPFlashToolV6` 相对 V5 多出的 GL 面与 KRB5 面，Qt5 本体在
   包内 `lib/`）：`agent_flash_prereq_packages_v6` + `install_agent.sh` 同一循环；#2133 同源
   守卫扩到 V6 那一组（四面逐项一致，防止「preflight 检查的」与「安装链装的」再分叉）。
8. `-b` 语义勘误入文档：两代 `-h` 都把 `-b` 定义为 `--reboot` 布尔，`auto` 是被工具忽略的
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
- **`qt-libs-v6` 做成硬门**：弃——552-only 主机被无关包名阻断，见 Decision 第 6 条。
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
  `backend/agent/tests/test_flash_preflight_v107.py` 8 passed；
  `backend/agent/tests -k "flash or required_tools or tool_cache or tool_asset or script_packages"`
  264 passed（含 v102–v105 preflight 套件与 v1310/v1311 main 流程套件）；
  **Agent 全套 `backend/agent/tests` 2544 passed**（102s，无失败）；
  `tests/test_flash_provisioning_prereqs_2133.py` 16 passed；
  `tests/test_script_seed_governance.py` + `tests/test_pipeline_template_script_pins_2865.py` +
  `backend/tests/test_pending_activation_view_2931.py` 14 passed；
  `test_param_docs_content.py` + `test_param_docs_registry.py` +
  `test_plan_parameter_projection.py` 129 passed / 1 skipped；
  `-m compileall backend/ tools/ scripts/ tests/` rc=0。
- `scripts/run_gates.py check:quick` → **16 gates OK**；`scripts/run_gates.py check:pr` →
  **23 gates OK**（rc=0；含 tool-manifest / resource-anchors / gov-surface / agent-tests /
  pr-migrate）。
- 根 `tests/` 全量补跑与 CI 复跑结论记在 PR 评论（不以「命令跑过」代替结果）。
- 为签名变化同步的既有桩：`test_flash_firmware_v1310/v1311` 的 `_pick_flash_tool_exe` 桩改为
  带默认值的两参 lambda；`test_flash_preflight_v102` 的条目名集合加
  `qt-libs-v6` / `flashtool-v6`（两项确为本版新增）。
- CI 首跑（PR #3689 `pr-agent-tests`）红在 **census 计数钉**：`tests/test_check_resource_anchors.py`
  把声明面钉成 26 锚 / `A01..A26`，本单新增 A27/A28 后真实输出是 28 锚。已同步该守卫
  （28 锚 / `A01..A28`，类 docstring 写明「新增锚须在此同步计数；`--base` 的防扩张面抓的是把
  既有候选改判，不是新增候选的显式声明」），本地该文件 37 passed。
  **漏检根因：首跑只跑了选中的根文件，没跑根 `tests/` 全量**——CI 的根套件才是覆盖面。

未验证（不得当成已完成）：

- **Linux 侧 V6 从未真机跑过**。用户提供的成功日志来自 Windows 侧同代工具
  （`D:\WebTool\…`、`COM93`）；V6 的 `-b auto` 位置参数、退出码、stdout/stderr 分流、
  DA 态 USB pid 都只有 Windows 证据。
- NX2 的 `ro.product.model` 与 `ro.build.version.incremental` 字面值未取——路由键、版本目录名、
  `manifest.version` 三处一致性必须由真机读数钉死。
- 站点未发布 `flashtool_v6` 包，未 `--publish` 脚本包，未 scan，未建/重指 Plan。
  **顺序红线**：先发 `flashtool_v6` 包，再 scan/重指；两个新脚本版本都声明该工具，
  包未就位时声明它的版本整步 exit 2（含 552 的刷机计划）。
- `super` 分区 4.67G 的首刷耗时未测（`timeout_seconds` 默认 1200s 沿用 552 口径）。

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
- 若出现「同一 host 并刷 V5 与 V6 机型」的场景，host 级 flock 仍保证每 host 同时 1 台，
  但门控 pid 集合需按机型合并——现在是一份全局集合，无代际维度。
