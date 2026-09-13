# #507 A 节：存量脚本「设备路由吸收执行差异」普查

Status: implemented
Class: process

基线 `origin/main@9301f76f`（2026-09-13）。判据按 #507 方法备注：以**协议字符串 /
代码路径**为准，不按符号名猜测；每项结论带 file:line。范围 = A 节「存量脚本普查」
（B 节准入项由 #705 承接，本文不重复）。

## Decision

**结论：存量 32 族中真正需要机型路由的 2 族全部达标（三要素齐全）；未发现「约束
落空」实例。** 两个跟进点：G14 资产分发（#711）、B 节 checklist 固化（#705）。

判据（ADR-0029 v2）：行为随设备而变的脚本，必须自己读设备指纹路由、未匹配
**fail-fast**、路由决策进 step_trace；「adb 指纹读不出的部分」才归登记簿。

### 全量清单（32 族 × 最新版本）

| # | 族（最新版） | 行为随机型/平台变？ | 吸收方式 | 判定 |
|---|---|---|---|---|
| 1 | `flash_firmware` v1.3.15 | **是**（机型族 → 固件目录） | **脚本内指纹路由**（`_MODEL_FAMILY_ROUTES`） | ✔ 样板 |
| 2 | `monkey_test` v1.2.2 | **是**（AD11 例外分支） | **脚本内指纹路由**（`route` 决策进 metrics） | ✔ 样板 |
| 3 | `gpu_check/setup/finish` v1.0.10/v1.1.0/v1.0.3 | 部分（MTK 专属步骤） | 脚本内**尽力而为**（非 MTK 失败不阻断，明示注释） | ✔ 可接受例外 |
| 4 | `sleep_*` / `powercycle_*` | 部分（需机型匹配的 platform 签名 APK） | 带外资产 + **安装/校验失败即报错**（fail-closed） | ✔ 例外（依赖 #711 分发缺口） |
| 5 | `aee_prepare` v1.0.1 | 否（MTK 域脚本，vendor prop 读写） | 脚本自校验（setprop 后回读，失败即错） | ✔ |
| 6 | `mtbf_setup` v1.4.1 / `oobe_skip` v1.1.2 | 否 | 读 `build.type/debuggable`、`sys.boot_completed` 属能力/状态探测，非机型分支 | ✔ |
| 7 | 其余族（含 `install_apk` / `push_resources` / `monkey_setup` / `connect_wifi` / `fill_storage` / `clean_env` / `monkey_check` / `monkey_teardown` / `stop_aimonkey` / `noop` 等） | 否（或差异在项目维度） | 调用方参数 / 流程编排 | ✔ 见下节 |

**指纹读取族（9）**：`aee_prepare` / `flash_firmware` / `gpu_check` / `gpu_finish` /
`gpu_setup` / `monkey_test` / `mtbf_setup` / `oobe_skip` / `powercycle_finish`。
其中**只有 2 族**把指纹用于**机型路由**；其余 7 族读的是能力/状态属性
（`ro.boot.ddrsize` RAM、`sys.boot_completed`、`ro.build.type`、`persist.vendor.
mtk.aee.mode`），不产生机型分支 → 不存在「本应路由却没路由」。

### 三要素核验（两个路由样板）

| 要素 | `flash_firmware` v1.3.15 | `monkey_test` v1.2.2 |
|---|---|---|
| 指纹 → 路由表 | `_MODEL_FAMILY_ROUTES`（:637） | AD11 例外分支（:208） |
| 未匹配 **fail-fast** + 已知集合 | :1272-1277 `no firmware family route for model …; known models: …` | :210-221 AD11 专属脚本缺失 → `refusing silent fallback` |
| 路由决策进 step_trace/metrics | `route` 进 metrics（:1536/:1547），`decided_by=params|fingerprint`（:1140/:1178/:1334） | `metrics.route = {decided_by, model, branch}`（:193） |
| **指纹来源差异鲁棒** | 下划线/连字符双拼写键都收（:642-643；现场教训 :174-177） | 子串匹配 `"AD11" in model`，两种拼写均命中 |

**闭集白名单核对**（#507 重点项）：

- `flash_firmware`：**闭集 + fail-fast**——新机型未登记时不会静默走默认，直接失败并
  列出已知集合（:1272-1277）✔；
- `monkey_test`：**刻意非闭集**——unknown 机型走通用脚本（`generic` 是合理默认，
  AD11 是例外分支；docstring :190-193 明示）。残余风险=新机型若需要例外脚本会被
  静默归入 generic；缓解=`metrics.route` 可观测、可事后审计。**判定：设计取舍，
  可接受，已在 docstring 登记。**

### 「本应路由却没路由」逐项判定

扫描 `args.get("<含 model/family/platform/variant 的键>")` 全库：**仅
`flash_firmware.family`**（显式覆盖口，缺省时仍走指纹路由，且 `decided_by` 记录
参数 vs 指纹）——**不存在把机型差异推给调用方的参数**。

重点候选 `install_apk`（#507 点名）：`apk_path` / `required_version` / `pkg_name`
（v1.1.0 :37-47）纯参数驱动，不读指纹。**判定：可接受**——差异维度是**项目/用例**
（同一设备跑不同项目要装不同 APK），不是「adb 指纹读得出的设备类型」；脚本即使
读指纹也无法推断该装哪个项目的用例包。此类差异按 ADR-0029 属登记簿/编排层，
**不构成约束落空**。建议把该判定固化进 B 节 checklist（#705），避免后续评审重复争论。

同类（`push_resources.files/manifest`、`monkey_setup.steps`、`connect_wifi.ssid`、
`clean_env.uninstall_packages` 等）同判：**编排/项目维度参数，非机型路由缺口**。

### 发现（供 B 节 checklist 与后续自检）

1. **良性模式值得推广**：powercycle 的 Z2582 前台服务拦截问题，处理方式是
   **把 workaround 对所有机型统一生效**（先 Activity 再 start-foreground-service，
   `powercycle_setup/v1.1.0/_lib.py:502-505`），而非按机型分支——"能用统一序列
   吸收的差异就不要分支"，从根上消除闭集白名单问题；
2. **平台域脚本的例外**：`gpu_*` 的 MTK 专属步骤"尽力而为、失败不阻断"，已在
   `_lib.py:14-15/:296/:330-355` 明示（生产只扫 MTK，#220）；`sleep/powercycle`
   依赖**机型匹配的 platform 签名 APK**，当前带外部署、安装失败即报错
   （`powercycle_setup.py:85` 给出手工指引）——fail-closed 故非静默失败，但
   **分发自动化缺口已登记为 G14（#711，依赖未开工的 #461）**；
3. `stop_aimonkey` 默认 force-stop 列表含 `com.transsion.MkWatchdog`
   （v1.0.1 :124）——**附加式**默认（不存在该包的机型上 force-stop 无害），
   不构成机型分支；
4. **未发现**「闭集白名单静默走默认分支」实例（flash_firmware 会 fail-fast，
   monkey_test 的 generic 是明示设计）。

## Alternatives

- **逐脚本实测运行时行为**（慢设备/异常网络跑一轮）——放弃：无设备/环境且成本
  远超本自检目的；ADR 约束的判定对象是**代码路径**（是否读指纹、是否 fail-fast、
  是否记录决策），静态审计即可证伪「约束落空」，实测留给专项真机批次；
- **把「项目维度参数」（install_apk 等）一律判为约束落空**——放弃：ADR-0029 的
  路由约束以「adb 指纹读得出的部分」为界；项目/用例维度不可由设备指纹推断，
  判为落空会把登记簿/编排层的正当职责误判为违规（该口径已写入本文，供 B 节复用）；
- **要求 monkey_test 改闭集白名单**——放弃：其 generic 分支是刻意的合理默认
  （AD11 为例外），改闭集会让所有新机型 fail-fast，与「非闭集白名单」的既有
  决策（v1.2.0 docstring）冲突；以 metrics.route 可观测性作为缓解即可；
- **顺带处理 G14（#711）/ B 节 checklist（#705）**——放弃：均已有承担载体
  （#711 依赖 #461；#705 已有在途 PR），本单只做 A 节普查与判定留痕。

## Verification

实际执行（worktree `/tmp/stp-507`，基线 `origin/main@9301f76f`）：

1. **全量族清单**：Python 脚本遍历 `backend/agent/scripts/*/`（32 族），每族取
   最新版本，统计 `getprop` / 路由表 / fail-fast / PROGRESS / `capabilities.json` /
   参数键——输出即 Decision 中的清单表；
2. **指纹用途分类**：对 9 个 `getprop` 族逐族 grep 属性名与上下文（`aee_prepare`
   →`persist.vendor.mtk.aee.mode`；`gpu_*`→`ro.boot.ddrsize`；`mtbf_setup`
   →`ro.build.type|ro.debuggable`；`oobe_skip`/`powercycle_finish`
   →`sys.boot_completed`）；
3. **机型路由面 airtight 复核**：全库最新版本 grep `getprop (ro.product|ro.board|
   ro.hardware|ro.boot|persist.vendor)` → `ro.product.model` **仅**出现在
   `flash_firmware/v1.3.15:174,205,634` 与 `monkey_test/v1.2.2:112` 两族，
   与「只有 2 族机型路由」一致；
4. **硬编码机型字面量扫描**：`MLD[-_]?LX|AD11|X6851|Z2582|Infinix|transsion|honor`
   → 无「按机型分支且无 fail-fast」实例；命中项均为注释/附加式默认（Z2582 注释、
   `com.transsion.MkWatchdog` 附加默认）；
5. **三要素逐点核验**：flash_firmware `:637/:1272-1277/:1536-1547/:642-643`；
   monkey_test `:193/:208-221`（行号见 Decision 表）；
6. `check:quick` → 全绿（本文件过 S10 四节契约）。

## Revisit

- **复扫触发条件**：新增读 `ro.product.model` 的脚本、新增机型分支、或脚本族
  大幅改版后，按本文三条 grep 重扫（`getprop` 指纹面 / `_MODEL`+route 表 /
  `args.get("<model|family|platform|variant>")`），并在本表追加版本行；
- **G14 落地后**（#461 → #711）：把 sleep/powercycle/gpu 的机型匹配 APK 从
  「带外 + fail-closed」升级为经上传/下载 API 分发，并复核其路由/校验路径；
- **B 节 checklist（#705）合入后**：把本文「项目维度参数不判落空」的口径并入
  checklist 的判定说明，避免后续评审重复争论 install_apk 类参数驱动脚本；
- **平台扩列（QCOM 等）**：一旦出现非 MTK 平台脚本族，按本表模式先核
  fail-fast/step_trace 两要素，再纳入清单。
