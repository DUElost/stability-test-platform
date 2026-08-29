# MTBF 专项 P0 设计：脚本三件套 + realresult 解析 + 配置/产物通道

- 日期：2026-08-19
- 状态：**设计草案**（供 P0 实施 PR 引用；评审意见「下一步应是 P0 设计 PR」的直接产出）
- 上游：[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md) D6 P0（Proposed）、[研究文档](../reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md) §5.3/§5.5
- 关闭的开放问题：评审 P0 阻塞项 ① realresult schema（§2，已读透反编译代码定稿）、② 工具目录可达（§4，推荐方案）

## 0. 结论摘要

1. **realresult schema 已定稿**（§2，来源：`apps/OfflineScriptManager/apk_sources` 反编译代码）：每次运行一个目录
   `{sdcard}/results/realresult/{yyyy.MM.dd_HH.mm.ss.SSS}/TESTS-RealResult-TestPoints.xml`，`<testpoint>` 按轮次累积
   （同名多条），**`id` 恒为 0**——解析必须以 **testpoint name 为 join 键**，状态从 testcase 子元素派生。
2. **脚本三件套** `mtbf_setup`（init）/ `mtbf_check`（patrol，PROGRESS 打戳）/ `mtbf_finish`（teardown），
   从 `deploy/run/stop.ps1 + lib.ps1` 移植为 Python；params 见 §3.2。
3. **配置/产物通道推荐**：清单与全局参数放**中心存储** `{STP_AEE_NFS_ROOT}/mtbf/{project}/`（控制面导出 / Agent 直接读），
   APK 放 **Agent resources 目录**（`aimonkey` bundle 先例）；逐条结果写 `{STP_AEE_NFS_ROOT}/mtbf/{project}/results/{run_dir}.json`，
   为 P1 export / P2 `test_case_result` 铺路。
4. **结果回填**：摘要 metrics + `suite_sha256` 走 step_trace（stdout JSON）；**逐条结果不进 stdout**
   （`step_trace.output` 64KiB 截断，`_MAX_STEP_OUTPUT_CHARS`）；P0 不扩 JobArtifact 白名单（评审定调）。
5. **预览/校验 API**：单端点 `POST /api/v1/mtbf/runtask/validate`（multipart 主路径），预览 + 校验合一。

## 1. P0 目标与边界

**目标**：MTBF 专项在平台跑通——一个 Plan（三步骤）真机执行，PlanRun 详情可见用例摘要；断连/停跑可恢复；清单 sha256 留痕可复现。

**边界（明确不做，P1/P2 再做）**：`test_suite`/`test_case` 表与 CRUD；前端新页面；JobArtifact 白名单扩展；
用例级结果入库（`test_case_result`）；D3b 项目匹配门禁（无 `test_project` 前以 `project` 字符串参数占位）。

## 2. 设备端事实（realresult 采样，反编译实测）

> 来源文件（`/mnt/automation-toolkit/android-tools/apps/OfflineScriptManager/apk_sources/sources/`）：
> 写入 `b/b/a/a/b/d.java`（XML 序列化）、结果线程 `utils/l/f.java`、目录/命名 `utils/g.java`、`utils/f.java`、
> 结果收尾 `view/RunTaskService.java`、结果解析 `utils/m/f.java`、状态枚举 `b/b/a/a/b/g.java`。

### 2.1 位置与命名

| 项 | 值 |
|----|----|
| 结果文件 | `{sdcard}/results/realresult/{run_dir}/TESTS-RealResult-TestPoints.xml` |
| run_dir | 运行开始时刻 `yyyy.MM.dd_HH.mm.ss.SSS`（`utils/g.java:q()`，本地时区）——**每次 RunTaskService 运行唯一** |
| 写文件时机 | 运行开始创建（`d.java:c()`，含 `<testpoints taskname>` 头），**运行结束才 close**（`d.java:a()`） |
| 轮次语义 | 文件创建一次，**各轮次 testpoint 追加写入**：130 条 × N 轮 → 文件内同名 `<testpoint>` N 条 |
| 其他产物 | `{results}/Log/{run_dir}/log.txt`（运行日志）、`{results}/record_data/{run_dir}/battery_data`（电量）、`{results}/ScreenShots_crash/`（崩溃截图） |

### 2.2 XML schema（精确，writer `d.java` 逐字段）

```xml
<testpoints taskname="{runtask name 属性值}">
  <testpoint id="0" name="{testpoint name}" tests="{该次执行的 testcase 结果数}"
             failures="0|1"            <!-- 仅区分 testpoint 状态 == FAILURE；ERROR/INCOMPLETE 也是 0 -->
             time="{ms}" starttime="{ms}" endtime="{ms}"
             [startbattery="%"] [stopbattery="%"] [regression="{回归重跑次数}"]>
    <testcase type="uiautomator2" serialnumber="..." devicename="..." command="{adb 命令}"
              package="..." classname="..." name="{method}" time="{ms}" starttime="{ms}" endtime="{ms}"
              [screenshot="{截图路径，空则无此属性}">
      <!-- 仅非 PASS 时出现子元素 -->
      <failure>{消息}</failure>     <!-- 状态 == FAILURE -->
      <error>{消息}</error>         <!-- 状态 == ERROR 或 INCOMPLETE -->
    </testcase>
  </testpoint>
</testpoints>
```

- 状态枚举（`b/b/a/a/b/g.java`）：`PASS/FAILURE/ERROR/INCOMPLETE`（`RUNNING` 仅运行期瞬态，不落盘）。
- 转义：属性值 `& < > " '` 五类实体转义；`<failure>/<error>` 文本中 `\u0000` → `<\0>`（解析时还原）。
- **`id` 恒为 0**：writer 模型 `b/b/a/a/b/h.java` 的 id 字段无任何赋值路径（已核实 `f1362a` 仅默认值）——
  **join 键必须是 `name`**，`id` 不可用。
- testcase 属性 `command` 为完整 adb instrument 命令（含 args），`classname/name` 即 class/method。

### 2.3 状态派生规则（解析器必须按此实现）

- testcase：有 `<failure>` → FAILURE；有 `<error>` → ERROR；否则 PASS（INCOMPLETE 在落盘时归入 `<error>`，
  无法与 ERROR 区分——解析器统一报 `ERROR/INCOMPLETE` 或按 message 内容尝试区分，P0 取前者）。
- testpoint：任一 testcase 非 PASS → 该 testpoint 非 PASS（`failures="1"` 仅作 FAILURE 提示，不作权威）；全部 PASS → PASS。
- 轮次统计：同名 `<testpoint>` 出现次数 = 该用例已跑轮数（含回归重跑，精确轮数需按 regression 属性折算，P0 不做折算，只报条目数）。

### 2.4 进度信号源（`mtbf_check` 的 PROGRESS 依据）

| 信号 | 命令（设备端） | 用途 |
|------|----------------|------|
| 运行目录出现 | `ls /sdcard/results/realresult/` | 已开始 |
| 用例完成数 | `grep -c '<testpoint ' {run_dir}/TESTS-RealResult-TestPoints.xml`（追加写，mtime 变化） | 进度 = 已完成用例条目数 |
| 日志活度 | `ls -l /sdcard/results/Log/{run_dir}/log.txt`（大小增长） | 存活补充信号 |
| 服务存活 | `dumpsys activity services com.ape.offlinescriptmanager` | 看门狗语义（见 3.4） |

> 注意：结果文件追加写、`adb pull` 中途快照安全（pull 的是拷贝）；但 patrol 周期内**用 shell 统计而非全量 pull**
> （长跑文件可达 MB 级）。**计数必须用带尾空格的 `<testpoint `**（根元素 `<testpoints ...>` 与 `<testpoint` 前缀重叠，
> 尾空格可精确排除根元素：`<testpoints` 的 `s` 不匹配 `<testpoint `）；golden 测试须含「含根元素时的计数」样例。
> `grep -c` 不可用时回退 `wc -l`（testcase 行数与 testpoint 同阶，仅作量级参考）。

### 2.5 遗留行为（记录，P0 不处理）

- 运行结束设备端会把结果经 **ZMQ 上传旧 MTBF 平台**（`utils/l/h.java`，`tcp://172.16.x.x:6906`）——平台化后属冗余
  上报；内网测试机接受，不处理（如需禁，属设备端 APK 改造，另议）。
- 设备端看门狗 `MtbfAutoResumeReceiver`：30 分钟检查拉起 + `BOOT_COMPLETED` 自动续跑（`auto_resume` prefs 控制）——
  **平台 patrol 的「服务死亡」判定必须考虑看门狗会自行恢复**（见 3.4）。

## 3. 脚本三件套契约

### 3.1 通用契约（对齐现有脚本先例 `monkey_*` / `flash_firmware`）

| 项 | 约定 |
|----|------|
| 环境变量 | `STP_DEVICE_SERIAL`（必填）、`STP_ADB_PATH`（默认 `adb`）、`STP_STEP_PARAMS`（JSON） |
| stdout | 单行 JSON：`{"success": bool, "error_message": str?, "metrics": {...}}`（step_trace output 全量保留） |
| PROGRESS | stderr `PROGRESS {"seq":N,"step":...,...}`（#115 协议，仅 `mtbf_check` 必须） |
| 退出码 | 0 = 成功；非 0 = 失败（与 stdout `success=false` 一致） |
| 幂等 | setup 可重跑（force-stop + 重推配置）；finish 可重跑（无运行任务时仅 pull+解析） |
| capabilities.json | `mtbf_check` 版本目录声明 `["progress_stamps"]`（stall_seconds 门禁前提） |
| adb 前置 | `adb root` 可用（prefs 写入需要）；WSL Agent `ANDROID_ADB_SERVER_PORT=5039` |

### 3.2 params 草案（`default_params`，版本内不可变；P1 起 suite 绑定走 ADR-0030 D2）

> **P0 实施修正（2026-08-19，实测平台惯例）**：scan 注册的脚本 `default_params` 恒为空
> （既有 monkey/flash_firmware 全部 `{}`；逐计划参数通道不存在，ADR-0029 D1 挂起）。
> 因此 P0 配置解析顺序为 **`STP_STEP_PARAMS` > `STP_MTBF_*` env（hot-update 可同步）> 代码默认**
> （`_lib.py:param_or_env`，aimonkey 的 cfg>env>内置默认 先例同款）。下表为 STP_STEP_PARAMS 的
> 预期键（未来 P1 经 default_params/suite 绑定注入时的字段名），env 键：
> `STP_MTBF_PROJECT` / `STP_MTBF_TASK_TIMES` / `STP_MTBF_TESTER` / `STP_MTBF_INSTALL_APKS` /
> `STP_MTBF_AUTO_RESUME` / `STP_MTBF_RESOURCES_DIR` / `STP_MTBF_EXPECTED_TESTPOINT_COUNT` /
> `STP_MTBF_DEAD_GRACE_CYCLES` / `STP_MTBF_FORCE_STOP`。

```json
{
  "mtbf_resources_dir": "/opt/stability-test-agent/agent/resources/mtbf",
  "project": "legacy",
  "task_times": 100,
  "tester": "tester",
  "install_apks": true,
  "auto_resume": true
}
```
> 代码默认 `mtbf_resources_dir` 为**相对 Agent 目录解析**（`_lib.py:_default_resources_root`，
> 部署布局 `/opt/stability-test-agent/agent/resources/mtbf`——含 `agent` 层级，2026-08-20 部署实测确认）。

- `project`：P0 为纯字符串（资源/配置子目录名，如 `mld` / `ela`）；`test_project` 落地后改为 `project_id` 映射（P1）。
- `task_times`：覆盖导出/部署的循环次数（评审定调：仅影响部署，`<=0` 表示保持 runtask.xml 原 `times`）。
- 中心存储路径不放进 params：脚本用 Agent 侧 `STP_AEE_NFS_ROOT` env 拼 `{root}/mtbf/{project}/`（env 单一来源，见 §4）。
- `expected_testpoint_count`：仅 `mtbf_check` 需要（见 §3.4）。P0 无 default_params 通道（§3.2 修正），取值经 `STP_MTBF_EXPECTED_TESTPOINT_COUNT` env 或代码默认（0=只报绝对数）；`mtbf_setup`/`mtbf_finish` 不需要。

### 3.3 `mtbf_setup`（init 阶段）

移植自 `deploy.ps1 + lib.ps1`（`Install-MtbfApks` / `Push-MtbfConfig` / `Set-MtbfPrefs` / `Set-MtbfDeviceStability` / `Start-MtbfTask` / `Test-MtbfSystemUid`）：

1. **解析资源**：`{STP_AEE_NFS_ROOT}/mtbf/{project}/runtask.xml` + `UiAutomatorTestData.xml`；`{mtbf_resources_dir}/{project}/` 下 3 个 APK——**精确文件名**：`OfflineScriptManager.apk` + `ReliabilityUiautomatorTest.apk` + `ReliabilityUiautomatorTestTest.apk`（与 `lib.ps1:Install-MtbfApks` 一致，勿用研究文档中「两个 Reliability*.apk」的缩写代替）。任一缺失 → fail-fast。
2. **留痕**：计算 `runtask.xml` 与 3 个 APK 的 sha256 → `metrics.suite_sha256` / `apk_sha256[]`（ADR-0029 v2.2 补偿机制；P0 无库，靠 step_trace 快照）。
3. **安装 APK**（`install_apks=true`）：先装两个用例 APK（`adb install -r`），OfflineScriptManager **先 `uninstall` 再 `install -r`**（签名/uid 变更场景，lib.ps1 同款）；装后校验 `dumpsys package ... | grep sharedUser` 为 `android.uid.system`，不符 fail-fast（对应 README「界面一直 Pass 但每条不到 1 秒」的经典坑）。
4. **推送配置**：runtask.xml 正则 patch `times`（`<runtask\b[^>]*\btimes="\d+"` → `task_times`，`<=0` 保持原值）→ `/sdcard/runtask.xml`；`UiAutomatorTestData.xml` → `/sdcard/`；随后 `appops set com.ape.offlinescriptmanager MANAGE_EXTERNAL_STORAGE allow`（`lib.ps1:Push-MtbfConfig` 同款，漏掉会写不了结果目录）。
5. **设备稳定性**：屏幕常亮、禁锁屏（`lib.ps1:Set-MtbfDeviceStability` 逐条移植）。
6. **写 prefs**：`adb root` 后 push 3 个 shared_prefs XML（`update_data.xml` `isUpdating=true`、`test_task_data.xml` `task_creator={tester}`、`mtbf_runner.xml` `auto_resume={auto_resume}`），`chown system:system` + `chmod 660`。
   - **adb root 是硬前置（v1.3.0 fail-fast）**：v1.2.0 曾忽略 `adb root` 失败，user 构建（`ro.debuggable=0`）上表现为晦涩的 `push ... rc=1`；v1.3.0 改为 `_ensure_adb_root()`——`adb root` 后以 `id -u` 校验（重试 5 次覆盖 adbd 重启窗口），失败即报错并带 `ro.build.type` / `ro.debuggable` 诊断与「需 userdebug/eng 工程包」指引。设备资格实证（2026-08-20 冒烟）：MLD-LX2 `use` 构建 `ro.debuggable=1` → 可 root；MLD-LX3 `use` 构建 `ro.debuggable=0` → 不可 root。
7. **启动**：`force-stop` → 启动 `BatteryActivity` → `am start-foreground-service ... RunTaskService -a ...action.start` → 广播 `MTBF_KEEPALIVE`（看门狗注册）。
8. **验证**：服务在跑 + 轮询（≤60s）等待 `{sdcard}/results/realresult/` 出现运行目录（`run_dir`）→ `metrics.run_dir`。
9. stdout JSON（`success` 见上）；失败路径：force-stop 清理 + `error_message` 带根因。

超时建议：步骤 `timeout_seconds=900`；多设备同 host 串行安装时 Plan `barrier_timeout_seconds` 需抬高（默认 600s 可能连坐，建议 1800）。

### 3.4 `mtbf_check`（patrol 阶段）

- `patrol_interval_seconds=300`，步骤 `stall_seconds=600`（必须 + PROGRESS），`timeout_seconds=0`（不限，长跑由停滞钟兜底——见 ADR-0030/研究 §5.3）。
- 每周期：
  1. **存活判定**：`dumpsys activity services` 查 `RunTaskService`。不存活 → **不立即失败**（设备端看门狗 30 分钟会拉起）；连续 2 个周期死亡且看门狗未恢复 → `success=false`（记 `mtbf_service_dead`）。
  2. **进度采集**：按 §2.4 统计 `testpoints_done`（`grep -c`）、结果文件大小/mtime、log.txt 大小。
  3. **PROGRESS 打戳**：`PROGRESS {"seq":N,"step":"mtbf_check","run_dir":...,"testpoints_done":X,"expected_per_round":E,"rounds_estimate":Y,"log_bytes":Z}`（`expected_per_round` 来源见下）。
  4. stdout JSON：`{"success": true, "metrics": {progress 同 PROGRESS 字段}}`。
- **`expected_per_round` 来源（P0 定稿）**：脚本跨步骤**拿不到** `mtbf_setup` 的 step_trace（各步骤 stdout 各落各的）。
  首选 = `STP_MTBF_EXPECTED_TESTPOINT_COUNT` env 预置（P0 部署级配置，按清单填写如 130，随清单变更同步更新；
  经 hot-update 同步，见 §3.2 修正）；未配置或为 0 时 PROGRESS 只报绝对数、不报百分比。P1 起由 suite 绑定（ADR-0030 D2：派发时注入/`run_context` 携带）自动填充，删除 env 预置。
- 边界：设备离线 → job 走既有 UNKNOWN/恢复链路，check 不自行判死；patrol 期间结果文件 mtime 长期不变 + 服务死 → 判死（配合停滞钟）。

### 3.5 `mtbf_finish`（teardown 阶段）

移植自 `stop.ps1 + lib.ps1`（`Stop-MtbfTask`）：

1. **停任务**：先写 `auto_resume=false`（防看门狗/开机续跑）→ `am start-foreground-service ...action.stop` → 等 5s → 未停则 `force-stop` 兜底（`-Force` 语义）。
2. **拉结果**：`adb pull /sdcard/results/realresult` → 本地临时目录；定位最新 run_dir 的 `TESTS-RealResult-TestPoints.xml`（以 setup 记录的 `run_dir` 为准）。
   - **v1.3.0 修正（冒烟 #217 实测）**：`adb pull` 目录语义会保留远端末级名——本地结构为 `{local}/realresult/{run_dir}/`，
     v1.2.0 误以 `{local}/{run_dir}/` 定位导致「结果文件缺失」；修正后含 adb 版本差异兜底。
3. **解析**（ElementTree）：按 §2.2 schema、§2.3 状态派生；**以 testpoint name 为 join 键**；聚合 rounds、统计 PASS/FAILURE/ERROR。
4. **摘要 metrics**：`{rounds, entries, testpoint_count, passed, failed, error, suite_sha256, run_dir, duration_ms}`；无结果文件 → `success=false`（或 `partial` 标记，P0 取失败 + error_message）。
5. **逐条结果落盘**：解析后的完整 JSON（testpoint 列表含 testcase/failure 消息）→ `{STP_AEE_NFS_ROOT}/mtbf/{project}/results/{run_dir}.json`（Agent 写中心存储——NFS 权限需在部署时确认可写，见 §4.4）→ `metrics.detail_uri`。
6. stdout JSON 只带摘要（**不带逐条**——64KiB 截断风险，见 §0.4）。

### 3.6 版本目录与文件布局

```
backend/agent/scripts/
├── mtbf_setup/v1.0.0/{mtbf_setup.py, _lib.py, capabilities.json}
├── mtbf_check/v1.0.0/{mtbf_check.py, _lib.py, capabilities.json}   # ["progress_stamps"]
└── mtbf_finish/v1.0.0/{mtbf_finish.py, _lib.py, capabilities.json}
```

共享逻辑（路径解析 / adb 封装 / realresult 解析 / runtask 解析渲染）放 `_lib.py`（`_` 前缀模块，scan 跳过、不计 entry sha——按 ADR-0020 辅助模块约定）。

## 4. 配置与产物通道（工具目录可达，三选一定稿）

### 4.1 问题

`runtask.xml`/`UiAutomatorTestData.xml`/APK 现住 `/mnt/automation-toolkit/...`（控制面/Windows 共享盘）。
脚本跑在 **Agent 执行机**（20 台 Linux host），不保证可达该路径——评审 P0 阻塞项 ②。

### 4.2 方案对比

| 方案 | 机制 | 优点 | 缺点 |
|------|------|------|------|
| **A. 控制面 export + Agent 拉取（中心存储）** | 清单/全局参数放 `{STP_AEE_NFS_ROOT}/mtbf/{project}/`（Agent 已挂载中心存储）；APK 放 Agent `resources/mtbf/{project}/`（`aimonkey` bundle 先例，带外部署） | 复用既有挂载与 resources 机制；P1 export 直接写同一路径（**消费面不变**）；小文件（~80KB）同步轻 | NFS 根下新增 `mtbf` 目录（存储角色表需补一行）；APK 更新走带外 |
| B. 同步挂载工具目录到 Agent | 挂载 `/mnt/automation-toolkit` 或 Windows 共享 | 文件原地即用 | 跨机共享挂载脆弱（ADR-0025 已取消过渡 UNC）；20 台机逐台维护 |
| C. Job 下发临时文件 | dispatcher 把清单内容嵌入/附送 pipeline_def | 无新路径 | 需新增控制面→Agent 文件下发通道（不存在）；130 条清单进 params 污染快照 |

### 4.3 推荐：方案 A（中心存储 + resources）

- **清单/全局参数**：`{STP_AEE_NFS_ROOT}/mtbf/{project}/runtask.xml`、`UiAutomatorTestData.xml`——P0 由控制面从工具目录手动/脚本同步，P1 由 `export-to-tool-dir` 写入（同一路径）。
- **APK**：`{mtbf_resources_dir}/{project}/`（Agent 本地，带外部署，参照 `aimonkey_paths.py` 的 bundle 解析先例）。
- **结果**：`{STP_AEE_NFS_ROOT}/mtbf/{project}/results/{run_dir}.json`（Agent 写回，控制面可读——P2 `test_case_result` 入库的数据源）。
- 与 PowerCycle 专项统一：同模式（配置走中心存储、工具走 resources），P0 实施时在专项接入 PR 里对齐目录约定。

### 4.4 中心存储目录布局（存储角色表增补建议）

```
{STP_AEE_NFS_ROOT}/
├── mtbf/
│   ├── {project}/runtask.xml            # 清单（P0 同步 / P1 export 写）
│   ├── {project}/UiAutomatorTestData.xml
│   └── {project}/results/{run_dir}.json # 逐条结果（Agent 写）
└── (既有 dedup/ devices/ jira/ 不变)
```

> 需在 `docs/design/2026-storage-roles-and-aliases.md` 的角色表中登记 `mtbf/` 用途与写权限方（Agent 写 results/、控制面写配置），P0 实施时一并补。

## 5. 平台侧最小改动（P0）

### 5.1 预览/校验 API（单端点）

```
POST /api/v1/mtbf/runtask/validate
  body: multipart file（runtask.xml，主路径） | json {"path": "<控制面可达路径>"}
  auth: 登录用户（只读）；admin 无额外限制
  200 → {"valid": bool, "issues": [{severity, code, message, testpoint}],
         "preview": {"suite_name", "root_config", "global_refs": ["@@gWifiName", ...],
                     "testpoints": [{name, times, exec_descs: [{class, method, args}]}]}}
```

- 输入源语义（评审定调、P0 写死）：multipart 上传为主（不依赖任何磁盘可达性）；`path` 方式仅当控制面本地可达时可用。
- 校验规则：XML 良构 / testpoint 名唯一 / method 非空 / `@@var` 引用在 global 文件有定义（需同时上传 `UiAutomatorTestData.xml` 才检此项，可选 multipart 第二文件）/ testcase schema。
- 解析/渲染逻辑放 `backend/services/mtbf_suite.py`（P1 的 import/export 与脚本 `_lib.py` 共用同一规则，两侧 golden 测试对齐）。
- 运维 curl 示例与鉴权说明见 [`docs/operations/mtbf-api.md`](../operations/mtbf-api.md) §1（P0 薄片，P1 端点定稿后补 §2）。

### 5.2 precheck 与留痕（P0 范围）

- 无库，无「库 vs 磁盘」比对（P1 才有，ADR-0030 D2）；precheck 沿用既有脚本 sha 校验。
- 留痕：`mtbf_setup` 的 `metrics.suite_sha256` 进 step_trace（output 全量）→ 同快照两次 run 可归因（ADR-0029 v2.2 补偿机制）。

### 5.3 结果回填路径（P0 定稿）

```
mtbf_finish stdout JSON（摘要） ──► step_trace.output（≤64KiB，安全）
mtbf_finish metrics             ──► step_trace 展示 / PlanRun 详情
逐条结果 JSON                    ──► {STP_AEE_NFS_ROOT}/mtbf/{project}/results/{run_dir}.json
                                     └─ metrics.detail_uri（step_trace 可点查）
```

- 不扩 JobArtifact 白名单（评审定调：P2 大文件/下载场景再扩 `report` 类）；不写 `report_json`（控制面合成，脚本不可写）。
- P2 `test_case_result`：控制面从 `{NFS}/mtbf/{project}/results/` 读 JSON 入库。

### 5.4 Plan 示例（冒烟）

```
Plan: MTBF-专项-冒烟（specialty=MTBF, project 占位 legacy）
  init     script:mtbf_setup    params:{project, task_times:1, tester}   timeout_seconds:900
  patrol   script:mtbf_check    params:{expected_testpoint_count:130}
           patrol_interval_seconds:300  stall_seconds:600  timeout_seconds:0
  teardown script:mtbf_finish   timeout_seconds:3600
  barrier_timeout_seconds: 1800     # 多设备串行安装，默认 600s 会连坐
```

## 6. 验证计划

| 层 | 内容 | 判据 |
|----|------|------|
| 部署前置（实施 checklist） | ① `docs/design/2026-storage-roles-and-aliases.md` 角色表补 `mtbf/` 行（用途 / 写权限方）；② 实测 Agent 对 `{STP_AEE_NFS_ROOT}/mtbf/{project}/results/` 的 mkdir + 写权限；③ 控制面同步 `mtbf/{project}/` 配置（清单/全局参数）到中心存储 | ①②③ 在冒烟前完成（避免「写了设计、部署没权限」拖到冒烟才发现） |
| 单元（无设备） | runtask 解析/渲染/校验：对 `config/runtask.xml` 快照做 golden 测试（130/137 计数、`@@var` 清单、times patch 正则） | 计数与实测一致 |
| 单元 | realresult 解析：构造含 failure/error/screenshot/regression/battery 的样例 XML；**进度计数样例须含根元素 `<testpoints>`（验证 `<testpoint ` 尾空格排除）** | 状态派生、name join、轮次聚合正确；计数不含根元素 |
| 冒烟 | 1 台真机，`task_times=1`，Plan 全链路 | PlanRun SUCCESS，详情可见摘要 metrics + suite_sha256 |
| 失败路径 | 中途 `force-stop` → finish 报错/partial；设备离线 → job UNKNOWN → 恢复 | 无假 SUCCESS、无静默卡死 |
| 幂等 | 同 Plan 连跑两次 | 新 run_dir 无冲突，旧结果不串 |
| 长跑（可选） | `task_times=100` 跑 2–3h | PROGRESS 每周期刷新、stall 不误判、结果文件增量正常 |

## 7. 与 P1/P2 的衔接与开放问题

- **P1 衔接**：import 从真实 runtask.xml 建库；export 写 `{NFS}/mtbf/{project}/runtask.xml`（与 P0 消费路径相同）——「管理面升级、消费面不变」落地点；`project` 字符串 → `test_project.project_id` 映射 + D3b 门禁。
- **P2 衔接**：`test_case_result` 数据源 = `{NFS}/mtbf/{project}/results/{run_dir}.json`；artifact 白名单扩展 `report` 供下载。
- 开放问题（P0 实施 PR 内关闭）：
  1. Agent 对 `{STP_AEE_NFS_ROOT}/mtbf/` 的**写权限**（results/ 子目录 mkdir+写）——部署时实测确认，不行则结果先落 Agent 本地再由控制面收取（回退路径）；
  2. APK 安装幂等策略：`install_apks=true` 时是否每次重装（建议：versionName 相同则跳过，节省 7 天长跑重启场景的部署时间）；
  3. 设备端 `grep -c` 可用性（MTK 工程机 busybox）——不可用回退 `ls -l` 文件大小估算；
  4. 真机采样校验：§2 的 schema 结论以一次真实运行结果文件复核（评审「实跑一轮采样」的最终闭环）。
