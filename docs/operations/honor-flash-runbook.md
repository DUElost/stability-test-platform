# Honor 刷机 Runbook（MLD 先行）

> 配套设计：[`docs/design/2026-08-honor-flash-firmware-routing.md`](../design/2026-08-honor-flash-firmware-routing.md)；
> V6 代际（535 / MLD_NX2）见 [`docs/design/2026-10-mtk-spflashtool-v6-generation.md`](../design/2026-10-mtk-spflashtool-v6-generation.md) 与本文 §7。
> 本文是操作手册：固件上架 → 建计划 → 单台验证 → 放量。

## 0. 前置条件

- `flash_firmware` **最新 active 版本**已注册（`GET /api/v1/scripts?name=flash_firmware`
  能看到目标版本且 `is_active`）。**本文不钉死版本**：执行前按 §3 的命令取最新 active；
  文内出现的版本号是 **2026-09-15 复核值 `v1.3.16`**。
- 当前目标 Agent fleet 已 hot-update / restart 到含 v1.3.x 的 code revision。
- 目标固件刷机包已从研发渠道拿到（解压后含 scatter 与 DA 文件）。
- 目标机型走 V6 代际时（§7）：`flashtool_v6` 工具包**必须**先发布到站点 `packages/`，
  再 scan 注册 `flash_firmware@1.3.19` / `flash_preflight@1.0.7`。
- 串行刷多台**首刷**设备时，脚本会在每台刷完后多等一段「boot 稳定窗口」
  （默认 20s，`boot_stabilize_seconds`，v1.3.7 起）；指纹只取 MTK 口（v1.3.10 起收窄），
  邻机非 MTK USB 抖动不会重置窗口
  （[Agent Note](../notes/feature/2026-08-30-flash-v137-boot-stabilize-lock.md)）。
- **为何可以取最新**：v1.3.16 起门控回落改经 wrapper 窄面，host 上 wrapper 缺失/版本旧时
  只降级为 `no-priv-face` 并如实记录（非致命）；此前各版本的刷机时序修复（BROM 窗口、
  超时 SIGKILL、门控口持久化）都在最新版里。**脚本发布新版本后应复核本节与 §3**。

## 1. 固件上架（中心存储）

```bash
ROOT=/mnt/stp-aee/firmware
VER=8.0.1.100            # 以刷机包实际版本号为准

mkdir -p "$ROOT/MLD/$VER"
# 刷机包内容解压进 $ROOT/MLD/$VER/（scatter / DA / 分区镜像）

cat > "$ROOT/MLD/$VER/manifest.json" <<'EOF'
{
  "family": "MLD",
  "version": "8.0.1.100",
  "version_prop": "ro.build.version.incremental",
  "scatter_file": "<scatter 文件名>",
  "da_file": "<DA 文件名>",
  "models": ["MLD_LX2", "MLD_LX3"]
}
EOF

# 版本指针：v1.3.6+ 支持 per-model 映射（同族 LX2/LX3 并行）
cat > "$ROOT/MLD/latest.json" <<'EOF'
{
  "versions": {
    "MLD_LX2": "8.0.1.100",
    "MLD_LX3": "8.0.1.101"
  }
}
EOF
# 旧单键 {"version": "..."} 仍兼容；仅一种机型时可继续用单键
```

注意：

- `version` 必须与设备 `getprop ro.build.version.incremental` 的值同口径
  （比对就靠它）；不确定时先在一台设备上 `adb shell getprop
  ro.build.version.incremental` 确认版本字符串格式。
- **构建后缀陷阱（2026-08-28 实测）**：目录名可能带 `_FTM_userdebug` 等
  后缀，但设备 `ro.build.version.incremental` 返回**不含后缀**的串。
  manifest.version 必须以真机实测值为准（三处一致：目录名/manifest/
  latest.json）；否则 post-flash verify 恒 mismatch 误判失败。
- **userroot 包 incremental 陷阱（2026-11-13 实测，run 1339/1340/1342）**：
  userroot 构建变体的 `ro.build.version.incremental` 带 `user-` 段
  （如 `P535AA-HONOR-NX2-16-user-261110V94`），而包名与 boot.img 内嵌
  fingerprint 均不含该段——目录名（=包名口径）与设备 incremental 无法
  同串，fingerprint 路由的「目录名/params.version/manifest.version 三元
  一致」约束不可满足（改 params.version 则目录 not found，改目录则
  verify mismatch）。运维处置：此类包（NX2/NX3/NX3Carrier）一律改用
  **显式 `firmware_dir` 路由**——目录名沿用包名口径，manifest.version
  填真机 `getprop ro.build.version.incremental` 实测值（显式模式免
  目录名一致校验，比对按 manifest.version 执行；NX3Carrier 的
  fingerprint 形 manifest 同模式，见 plan 183/186/187）。验包取证必须
  同时提取 boot fingerprint 与真实 incremental，二者不等是 userroot
  包的常态而非异常；manifest.version 以 canary 刷后
  `post_flash_verify` 回读值为终审。
- **v3 复盘补充（2026-11-13，run 1342/1343 全批实证）**：canary 报
  `post_flash_verify` mismatch 后，运维**不得**只改 manifest.version 就放量
  ——fingerprint 路由会把 params.version 拼进目录，改串即整批
  `firmware_dir not found`（125 台秒败、无烧写副作用）。放量的前置检查：
  `curl -s /api/v1/plan-runs/{run_id}/jobs` 查 canary 的 flash 步骤
  `metrics.route.decided_by`——值非 `params` 时，运维必须改用显式
  `firmware_dir` 的 Plan（§3 参数形态见复盘文档 §3）。完整时间线、
  判据矩阵与失败签名分类见
  [11-13 复盘](./incident-2026-11-13-535-userroot-v94-flash-routing.md)。
- manifest 的 `version` 必须等于目录名，放错目录会 fail-fast。
- ELA 上架同样步骤，目录换成 `$ROOT/ELA/`，models 填 `ELA_LX2`/`ELA_LX3`。
- **版本切换 = 改 latest.json（族级指针，同族全部机型共享）**：多机型
  多固件并存时需按机型错开派发（models 白名单兜底防误刷）。per-model
  版本映射方案见
  [2026-08-28-flash-plan-version-mechanism](../notes/architecture/2026-08-28-flash-plan-version-mechanism.md)。

## 2. 鉴权（运维 curl 模式）

```bash
# 凭据边界见 production-diagnostics.md
source /home/debian13/stability-test-platform/.env.backend
TOKEN=$(curl -s -H "X-Agent-Secret: $AGENT_SECRET" \
  -F "username=$STP_ADMIN_USER" -F "password=$STP_ADMIN_PASSWORD" \
  http://127.0.0.1:8000/api/v1/auth/token | jq -r .access_token)
AUTH="Authorization: Bearer $TOKEN"
```

## 3. 建「MLD 刷机」Plan

init 三步：刷机 → 设备体检 → 重新拿 root（刷机会重置设备，root 必须重做）；
patrol 用 noop 维持心跳。

刷机步骤的 `script_version` 取最新 active（§0 判据；`$AUTH` 见 §2）：

```bash
FLASH_VER=$(curl -s -H "$AUTH" "http://127.0.0.1:8000/api/v1/scripts?name=flash_firmware" \
  | jq -r '[.data[] | select(.is_active) | .version] | sort_by(split(".") | map(tonumber)) | last')
echo "$FLASH_VER"     # 填进下面的 script_version（2026-09-15 复核值：1.3.16）
```

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/plans -H "$AUTH" \
  -H "Content-Type: application/json" -d '{
  "name": "MLD-刷机-8.0.1.100",
  "description": "Honor MLD 全量刷机（指纹路由 + 同版本跳过 + 刷后核验）",
  "barrier_timeout_seconds": 7200,
  "barrier_max_wait_seconds": 14400,
  "steps": [
    {"step_key": "flash", "script_name": "flash_firmware",
     "script_version": "1.3.16", "stage": "init", "sort_order": 10,
     "timeout_seconds": 2400, "stall_seconds": 900, "retry": 1},
    {"step_key": "check", "script_name": "check_device",
     "script_version": "1.0.0", "stage": "init", "sort_order": 20},
    {"step_key": "root", "script_name": "ensure_root",
     "script_version": "1.0.0", "stage": "init", "sort_order": 30},
    {"step_key": "idle", "script_name": "noop",
     "script_version": "1.0.0", "stage": "patrol", "sort_order": 10}
  ]
}'
```

参数依据（改之前先读懂再动）：

| 参数 | 值 | 依据 |
|------|----|------|
| `timeout_seconds=2400` | 40min | 脚本内 flash 1200s 默认 + 刷后核验等待 180s + 余量 |
| `stall_seconds=900` | 15min | 刷机阶段静默容忍（lock-wait/re-enumerate 期间都有打戳，900s 覆盖单阶段长静默） |
| `retry=1` | 失败重试 1 次 | 核验失败（版本不一致/设备未回来）重试有实际价值；更高先查根因 |
| `barrier_timeout_seconds=7200` | 2h | 先做完的设备在 INIT→PATROL barrier 等慢同伴：同 host 串行刷机，落差 ≈ (ceil(N/permit)−1)×单台耗时；13 台/host、单台 ~20min 时 3×20min=1h，留余量 |
| `barrier_max_wait_seconds=14400` | 4h 绝对硬顶 | 防 barrier 无限等 |

## 4. 单台验证（放量前必做）

```bash
# 1. 选一台 MLD 设备（确认 model=MLD_LX2/LX3、host ONLINE）
DEVICES=$(curl -s -H "$AUTH" "http://127.0.0.1:8000/api/v1/devices?model=MLD_LX2" | jq '.data[0].id')

# 2. 预览派发（不实际执行，检查会命中哪些设备/host）
curl -s -X POST http://127.0.0.1:8000/api/v1/plans/<PLAN_ID>/run/preview -H "$AUTH" \
  -H "Content-Type: application/json" -d "{\"device_ids\": [$DEVICES]}"

# 3. 正式触发
curl -s -X POST http://127.0.0.1:8000/api/v1/plans/<PLAN_ID>/run -H "$AUTH" \
  -H "Content-Type: application/json" -d "{\"device_ids\": [$DEVICES], \"note\": \"$FLASH_VER 首台验证\"}"
```

验证点（PlanRun 详情 / step_trace）：

1. flash 步骤 `metrics.route.decided_by`：fingerprint 路由下为
   `"fingerprint"`，`family == "MLD"`，`version` == manifest 版本；显式
   `firmware_dir` 路由下为 `"params"`，`metrics.firmware_dir` 指向实存目录，
   `version` == manifest 版本（userroot 包必须为此分支，见 §1）。
2. 刷前该设备已是目标版本 → 第二次跑同 Plan 应出现 `skipped: true`（快路径）。
3. 刷后 `metrics.post_flash_verify.current` == 目标版本。
4. 后续 check/ensure_root 步骤通过（设备刷完确实回来了）。

任一验证点不过 → 停止放量，按 step_trace 的 `metrics.route` /
`version_check` / `post_flash_verify` 字段定位。

## 5. 放量与观察

- 按 host 分批（先 1 台/host，再 1/3 机队，最后全量），串行刷机下每 host
  同时只有 1 台在刷，host 内排队自动进行。
- 关注 `saq`/Agent 日志中的 `lock-wait` 戳占比：等待时间是正常的（串行），
  但如果 lock-wait 时间远超单台刷机时长，说明该 host 排队过深，考虑分批错峰。
- 中止：`POST /api/v1/plan-runs/{run_id}/abort`（RUNNING 中的刷机会被杀进程树，
  该设备本轮标 ABORTED；设备处于 BROM 中途被断的，重新触发会从头刷）。

## 6. 已知边界

- **设备必须先回 Android 才能指纹路由**：adb 不可达（卡 BROM/黑屏）的设备
  路由会 fail-fast，此时用显式 `firmware_dir` 参数的步骤兜底（v1.1.0 行为）。
- **中止后的重派必须先行欠账放行**（2026-11-13 run 1336–1338 实证）：
  批量 abort 的 Monkey/专项 run 会给全体设备留
  `return_state.{gpu,monkey,power-cycle}=unverified`，新 run 卡 PRECHECK
  且不建 job。运维必须对每台执行
  `POST /api/v1/devices/{id}/return-override`（`families` 覆盖欠账族、
  `reason_category=manual_release_for_maintenance`、`evidence_ref` 必填，
  写明来源 run 与欠账 job_id）后再派发。
- **批量派发必须分批**（#3931）：一次派发量不得超过 agent 认领吞吐 ×
  UNKNOWN 宽限窗；135 台整批会触发未认领 job 误判
  `unknown_grace_timeout` 与 batch barrier `barrier_max_wait` 连锁判死。
  分批档位以 ~17 台/批、批间确认为准（11-13 复盘 §5）。
- **UNISOC（Z258）不适用**：SP Flash Tool 只覆盖 MTK；Z258 刷机另行立项。
- **吞吐上限 = 每 host 同时 1 台**：需要并行时按 USB 控制器分片锁是后续课题
  （设计文档 §8 重议触发）。
- **刷机后 30 分钟内 `battery_level` / `temperature` 可能是 0**（跟踪 #3702）：设备回总线的
  首拍慢指标采样落在首启未稳窗口，读到 0 后按 `_slow_sample_interval`（默认 1800s）保持整个
  窗口——2026-10-13 run 929 实测：20:07 刷机步收场即记 0，20:12/20:14/20:16 三次查询恒为 0
  （同期设备侧 `dumpsys battery` 实为 `level: 100` / `temperature: 320`），20:40 恢复 100/29。
  判据：拿 `adb shell dumpsys battery` 的实际 `level:` / `temperature:` 与设备页读数对照。
  **不得**用刷机后半小时内的电量/温度做阈值判定或告警收敛；要立即确认就以 adb 读数为据。

## 7. V6 代际机型（535 / MLD_NX2）

适用条件：目标机型的固件包由 SP Flash Tool **V6** 驱动（`download_agent/flash.xml` 形态），
例如 MLD_NX2（板级 P535AA / MT6835）。本节替换 §1 的上架动作；§2–§5 的建计划、单台验证
与放量流程不变。

> 警告（刷机窗口前必查，2026-10-12 实测）：NX2 所在四台 host（`172.21.x.x`，末段
> `.83–.86`，Debian 13）
> **未装 V6 的运行库**——`ldd SPFlashToolV6` 报 `libGL.so.1 => not found`，工具连 `-h` 都跑不起来。
> 补包属 provisioning：`apt-get install -y --no-install-recommends libgl1 libglx0 libglvnd0`
> （即 `agent_flash_prereq_packages_v6`，KRB5 面这 4 台已具备）。不补包时刷机步会在拿锁之前
> 以 `environment precheck failed: shared-libs: missing: libGL.so.1` 硬失败。

> 警告（混跑风险）：`flash_firmware` 的初始门控把 `0e8d:201c` 当刷机态隐藏，而实测
> `201c` 是 MLD-LX2/LX3/NX2 的**正常 adb 态** → 同 host 有其他测试在跑时，一次刷机会把邻机
> 一起摘线（判据问题见 #3690）。V6 单台验证请选择**无其他在跑作业**的 host 与时间窗。

> 警告（V6 刷机前必查目标可刷性，run 921 事故）：目标 adb 不可用（`no-device`）时**不得**
> 指望工具"等 USB"——不带 `-p` 的 V6 会去绑现场任何一个 preloader 口。`flash_firmware@1.3.20`
> 起 V6 默认三处 fail-closed：①按 serial 反查不到目标口 / 目标不可控 → 抢锁前判失败；
> ②命令行带 `-p <sysfs 口>`；③逐行核 `symbolic_link` 回显，绑错口立即杀工具并终止尝试环。
> 判据落在 metrics 的 `identity` / `port_binding` / `aborted` 三个字段，排查时先看它们。
> 救砖（设备已卡在 BROM/preloader、sysfs 读不到 serial）必须显式传 `flash_port=<口>`；
> 该参数只用于此场景，指定错口就是把固件刷进另一台手机。

> 警告：`flash_firmware@1.3.19` 与 `flash_preflight@1.0.7` 在包内声明了 `flashtool_v6` 工具
> 依赖（ADR-0051 D7）。任何人不得在 `flashtool_v6` 包发布之前 scan 注册或把 Plan 重指到这两个
> 版本——包未就位时**所有**刷机步骤（含 552 的 V5 计划）会在派发前 exit 2。

1. 发布 V6 工具包（源 = 中心存储 selector 解压物里的 `SP_Flash_Tool_V6`，包根取该目录内容）。

   ```bash
   python tools/dev/package_tool_asset.py \
     --src <中心存储>/SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100/SP_Flash_Tool_V6 \
     --name flashtool_v6 --version 1.2444.00.100 --kind tool --python-absent \
     --script-relative SPFlashToolV6 --write-manifest \
     --packages-root /mnt/stp-aee/packages
   ```

   预期：`packages/flashtool_v6/1.2444.00.100.tar.gz` 存在，且
   `sha256sum` 等于 `tool_manifest.json` 里 `flashtool_v6@1.2444.00.100` 的
   `package_sha256`（2026-10-12 登记值
   `3e3bc805a6376a989a3e3113b92ae259549bcef45dd219a556a5c2b72181e53e`）。
2. 读一遍目标机型的固件目录，确认入口物在位。

   ```bash
   ls <firmware>/<P535AA_...>/download_agent/flash.xml
   ```

   预期：文件存在。V6 的 DA 与 scatter 由 `flash.xml` 内部引用（`<dagent>` /
   `<scatter>`，按 flash.xml 所在目录相对解析），上架时**不得**把它们拆成命令行参数。
3. 在真机上取代际相关的两个值（补新机型时必做；`MLD_NX2` 已于 2026-10-12 实取代证）。

   ```bash
   adb -s <serial> shell getprop ro.product.model
   adb -s <serial> shell getprop ro.build.version.incremental
   ```

   预期（NX2 实测值，设备 `ATHTNX69****0085` @ 末段 `.83` 那台 host）：model = `MLD-NX2`
   （平台设备表记作 `MLD_NX2`，来源不同，路由表两个拼写都收）；
   incremental = `P535AA-HONOR-NX2-16-260928V9`。model 不在路由表已知集合时本步 fail-fast
   并列出已知集合，此时停止本流程按设计文档补路由键。
   **`ro.build.display.id` 不是比对口径**（NX2 上它是 `MLD-NX2-16-260928V9`，与
   incremental 不同形），上架目录名与 `manifest.version` 一律取 incremental。
4. 把固件包放入 `{STP_FLASH_FIRMWARE_ROOT}/MLD/<version>/`（`<version>` = 第 3 步读到的
   incremental 值），并写 manifest.json：

   ```bash
   ROOT=/mnt/stp-aee/firmware
   VER=<第 3 步的 incremental 值>
   mkdir -p "$ROOT/MLD/$VER"
   # 固件包内容放进 $ROOT/MLD/$VER/（含 download_agent/flash.xml）
   cat > "$ROOT/MLD/$VER/manifest.json" <<'EOF'
   {
     "family": "MLD",
     "version": "<同一个 incremental 值>",
     "version_prop": "ro.build.version.incremental",
     "tool_generation": "v6",
     "flash_xml_file": "download_agent/flash.xml",
     "models": ["MLD_NX2", "MLD-NX2"]
   }
   EOF
   ```

   预期：`cat "$ROOT/MLD/$VER/manifest.json"` 里 `version` 与目录名一致——不一致时
   `flash_firmware` 会 fail-fast（防放错目录）。v6 的 manifest **不需要** `scatter_file`
   与 `da_file`；写了也不参与 V6 命令行拼接。

   `version` 这个值必须等于设备里 `ro.build.version.incremental` 的**实际字符串**——它是刷后
   核验与 `skip_if_current` 的唯一判据，写错会把成功的刷机报成失败、或让该刷的设备被跳过。
   **不得凭目录名类推**：上架前先从包里取证（`super.img` 是 erofs 压缩、扫不出，用这三个）：

   ```bash
   for f in vbmeta.img boot.img vbmeta_vendor.img; do
     LC_ALL=C grep -aoE "[A-Za-z0-9._-]{0,28}<日期版本段>[A-Za-z0-9._-]{0,14}" "$SRC/$f" | sort -u
   done
   ```

   预期：三处给出同一个串（本次 `P535AA-HONOR-NX2-16-261013V21`），拿它当 `version`；
   三处不一致或取不到时停下来找固件团队，不要先刷再猜。
5. 更新族级版本指针（同族多机型并存，per-model 映射；不要整体覆盖别人的键）：

   ```bash
   cat /mnt/stp-aee/firmware/MLD/latest.json
   ```

   预期：改后仍保留 `MLD_LX2` / `MLD_LX3` 键，并新增
   `"MLD_NX2": "<version>"`（`MLD-NX2` 连字符拼写由脚本自动互转，二选一即可）。
   写入方式必须「临时文件 + `os.replace`」并 `chmod 0644`——CIFS 上以 0600 落盘的指针文件
   会让 Agent 侧读不到 `latest.json`（本手册 2026-10-12 踩过一次）。
6. canary 前确认批次现网版本：NX2 设备若已在目标 incremental 上（2026-10-12 现网 50 台
   均为 `P535AA-HONOR-NX2-16-260928V9`），`skip_if_current` 默认值会让本步以
   `skipped:true` 收场——那是判据正确的表现，**不会碰刷机工具**，也就验不到 V6。
   单台取证必须在步骤参数里显式写 `"skip_if_current": false`（在用 552 刷机计划不受影响）。
7. scan 注册脚本新版本，再重指或新建 Plan（顺序不可颠倒，见上方警告）。

   ```bash
   curl -s -X POST "$BASE/api/v1/scripts/scan" -H "Authorization: Bearer $TOKEN"
   ```

   预期：响应 `created` 含 `flash_firmware@1.3.19` / `flash_preflight@1.0.7`，
   `conflicts` 为空。随后按 §3 建计划（`script:flash_preflight` + `script:flash_firmware`
   两步都指向新版本），并按 §4 单台验证。
   单台 canary 的建计划载荷（**只挂一台空闲 NX2**；`skip_if_current` 必须显式给
   `false`，否则设备已在目标版本时本步以 `skipped` 收场、根本碰不到 V6）：

   ```bash
   curl -s -X POST http://127.0.0.1:8000/api/v1/plans -H "$AUTH" \
     -H "Content-Type: application/json" -d '{
     "name": "NX2-刷机-V6-单台canary-260928V9",
     "description": "535/MLD_NX2 SP Flash Tool V6 单机验证（flash_firmware 1.3.19）",
     "steps": [
       {"step_key": "precheck", "script_name": "flash_preflight",
        "script_version": "1.0.7", "stage": "init", "sort_order": 5,
        "timeout_seconds": 300},
       {"step_key": "flash", "script_name": "flash_firmware",
        "script_version": "1.3.19", "stage": "init", "sort_order": 10,
        "timeout_seconds": 3600, "stall_seconds": 900, "retry": 0,
        "params": {"skip_if_current": false, "verify_version": true}},
       {"step_key": "check", "script_name": "check_device",
        "script_version": "1.0.2", "stage": "init", "sort_order": 20},
       {"step_key": "root", "script_name": "ensure_root",
        "script_version": "1.0.2", "stage": "init", "sort_order": 30}
     ]}'
   ```

   预期：`id` 返回后，用 `POST /api/v1/plan-runs`（或设备页派发）**只选一台设备**跑；
   `retry=0` 是刻意的——首刷取证阶段不要让重试把两次真机窗口叠在一起。
   派发前先确认该 host 没有别的在跑作业（§7 顶部两条警告）。

8. 单台验证时核对四个 V6 判据（任一不符即回到设计文档 §6，不得放量）：
   - `step_trace` 的 `metrics.route.tool_generation` 为 `v6`；
   - `metrics.command_argv` 是 `[... , "-c", "firmware-upgrade", "-f", "<...>/download_agent/flash.xml", "-b", "auto"]`，
     且不含 `-d` / `-s`；**`flash_firmware@1.3.20` 起 V6 结尾还必须带 `-p <sysfs 目标口>`**——
     看不到 `-p` 就说明这一步跑的是 1.3.19 或更早（那版会把 DA 下发进任意一台邻机），立即停止；
   - `metrics.post_flash_verify` 的版本回读与 manifest 一致；
   - `metrics.port_binding.bound_port` 等于 `metrics.target_port`，且 `metrics.aborted` 为 `null`
     （`aborted` 非空 = 工具自报绑到了别的口、被脚本当场杀掉，属**止损成功**，必须查拓扑而不是重跑）；
   - `metrics.identity` 里 `port_source` 是 `serial`、`adb_state` 是 `device`；只有救砖才允许
     `explicit_param`（运维手填 `flash_port`），常规刷机看到它说明设备已脱离 adb，先查设备；
   - `metrics.gating.hidden`（V6 口径 `gate_pid_scope=brom`）里**不得出现正常 adb 态邻机的口**：
     拿派发前后的设备态对照，被隐藏的应当只有 `0003/2000/2001/2026/3000` 这些可刷态口；
   - 刷机窗口内出现过的 MTK pid（判据是 `_FLASH_STAGE_PIDS` 是否覆盖该机型）：

     ```bash
     lsusb -d 0e8d: | awk '{print $6}'
     ```

   预期：窗口内出现的 pid 全在 `0003/2000/2001/201c/2026/3000` 内。出现新 pid 时必须先发
   新版本补集合再放量——集合外的刷机态口不会被门控压制，工具可能抓到邻机。
   **别把这条读成「`201c` 该被隐藏」**：2026-10-12 实测 `201c` 是 MLD-LX2/LX3/NX2 的正常
   adb 在线态（正常态另有 `2046`），初始门控连它一起隐藏正是 #3690 要裁决的判据问题。

   > **2026-10-13 实测（V6 平台链路 canary 通过）**：plan 80 / run 929 / job 188321，
   > 设备 id 953（`AHFFNX69****0170`）在 `.86` 的**直连口 `1-6`**，目标版本
   > `P535AA-HONOR-NX2-16-261013V21`。`command_argv` 带 `-p 1-6`、`gating.hidden` 只有
   > `1-5.2.2`（那台 `0e8d:0003` 异常机）、同 host 9 台 `201c` 邻机未被压制、
   > `aborted=null`、`attempts=1` `outcome=ok`、`post_flash_verify.current == target`、
   > 五步链（preflight→flash→oobe_skip→ensure_root→check_device）全 `success=true`，
   > 刷机步耗时 **302.46s**。

   > **2026-10-13 实测（V6 同 host 批量，run 932 / plan 81）**：`.86` 一次派 9 台
   > （8 台真刷 + 1 台 `skipped`），**41 分 22 秒全绿**——刷机锁同 host 串行，均摊
   > ~4.6 分钟/台；批量耗时按「台数 × 单台刷机房时间」估，**不得**按并发估。
   > 其中 **5 台是 hub 嵌套口**（`1-5.1.2` / `1-5.3.3` / `1-5.2.4` / `1-5.1.4` / `1-5.4.1`），
   > 逐台 `bound_port == 目标口`、`aborted=null` ⇒ **hub 形态的 `-p` 已取证可用**
   > （原记「未取证」，现关闭）。9 个 job 的门控全为 `gate_pid_scope=brom`：7 个
   > `hidden=[]`（正常态邻机一台没压），另 2 个只压了现场那台异常机（`1-5.2.2` / `1-8`）；
   > 批量收场后该 host 10 个 MTK 口全部回到 `201c auth=1` 且 serial 可读——零残留、零伤害。
   > 仍未取证的是**混跑窗口**（同 host 同时有其它专项在跑）。
   >
   > **2026-10-14 实测（跨 host 批量，plan 82 / run 934、935）**：`.83` 11 台与 `.84` 10 台
   > 同一计划并行，54m14s / 57m58s 全 SUCCESS；21 台逐台 `bound_port == 目标口`、
   > `aborted=null`，收场后两台的 MTK 口全部回 `201c auth=1`、`2000` 口数 0。
   > **批量必改的一项**：`flash` 步 `timeout_seconds` 按「单台房时间 × 该 host 设备数 × 2」算——
   > 实测尾部设备 `duration_seconds` 已达 3440s（等锁计入本步），旧的 3600s 只剩 160s 余量，
   > 再多一台就会在下载中途被判超时杀工具。混跑仍未取证：与同时段 GPU 专项 `run 938`
   > （408 job / 32 host）逐 job 比对，落在 `.83/.84` 的 job 数为 0。

   > **2026-10-12 实测（NX2 V6 首刷）**：接管态是 `0e8d:2000`（在既有集合内，无新 pid）；
   > 但**刷机成功后 10–30 分钟**，设备会从「adb 可见、版本已回读一致」掉成
   > `0e8d:2046`——有 USB serial、**没有 adb 接口**，与本机位上长期 OFFLINE 的 `…0172` /
   > `…0226` 同形。此形态下远程无法复位（`authorized` 需 root，`stp-agent-priv
   > usb-authorized` 只接受提权调用），所以要**把 `oobe_skip` / `ensure_root` 紧跟在刷机步之后**
   > （别拖到 adb 窗口关掉），并把「现场按电源/重插」当作已知兜底动作写进放量前检查。


## 8. 发布后核验（新版本是否真的生效，四步全过才算）

适用条件：§7 的第 7 步（publish 脚本包 + scan）做完之后。**合入 / scan 都不算生效**——
判据是下面四条都绿（2026-10-13 本族 1.3.19/1.0.7 就是按这四条收口的）。

1. 站点包源与 Git 登记逐包对账（缺包/字节不符都会在这里露出来）。

   ```bash
   python - <<'PY'
   import hashlib, json
   from pathlib import Path
   g = json.loads(Path("tool_manifest.json").read_text(encoding="utf-8"))
   root = Path("/mnt/stp-aee/packages")
   for fam in ("flash_firmware", "flash_preflight", "flashtool", "flashtool_v6"):
       latest = [v for v in g["tools"][fam]["versions"] if not v["retired"]][-1]
       p = root / fam / f"{latest['version']}.tar.gz"
       ok = p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == latest["package_sha256"]
       print(f"{fam}@{latest['version']} sha相符={ok}")
   PY
   ```

   预期：四行全部 `sha相符=True`。
2. 注册面对账：head 版本没有「待激活」项。

   ```bash
   python -m backend.scripts.check_unreferenced_script_versions --pending-activation
   ```

   预期：不再列出 `flash_firmware@1.3.19` / `flash_preflight@1.0.7`。
   注：`kind=tool` 族（`flashtool*` / `aimonkey`）会被记成 `unregistered`——该视图按 `script`
   行对账，工具族本就没有 script 行，别把它当成本族的滞后账。
3. 库内激活态确认（`is_active` 才是派发依据）。

   ```bash
   curl -s -H "$AUTH" "http://127.0.0.1:8000/api/v1/scripts" \
     | jq -r '.data[] | select(.name|startswith("flash")) | "\(.name)@\(.version) active=\(.is_active)"'
   ```

   预期：`flash_firmware@1.3.19`、`flash_preflight@1.0.7` 均 `active=true`。
4. 刷机 host 侧可核验拉取（逐台重采，覆盖工具包预热）。

   ```bash
   for H in <NX2 host 列表>; do
     curl -s -X POST -H "$AUTH" "http://127.0.0.1:8000/api/v1/script-presence/refresh?host_id=$H" \
       | jq -r '.data | "\(.host_id) \(.counts)"'
   done
   ```

   预期：每台 `missing=0`、`mismatch=0`。`present/n_a` 计数只覆盖被 Plan 引用的版本，
   所以第 2/3 步不能省。

## 9. 重指存量计划的代价（V5/V6 同版本共存带来的一笔）

`flash_firmware@1.3.19` 与 `flash_preflight@1.0.7` 同时声明 `flashtool`（V5）与
`flashtool_v6`（V6），而 D7 是**声明即必达**：任一刷机步骤派发前，两代工具包都要在该 host
核验拉取。于是把 552 在用计划从 1.3.18/1.0.6 重指到新版，等于让那些 host 多拉
55.7MB（解包 139MB）。

- 追平账目：`python -m backend.scripts.check_unreferenced_script_versions --plan-step-drift`
  会列出仍钉旧版的活跃 Plan 步骤（2026-10-13 实测：plan 39/40/59/61/62/68 等）。
- 重指前按 #3030 口径过参数/预算契约核对（`metadata_compatible` ≠ 安全）；
- 不追平的代价：552 计划继续用 1.3.18，**拿不到 v6 分支的任何修复**（本单的
  `caught_brom` 代际判据、后续门控修正同理）。
- 想拆掉这笔耦合：只让 `flash_firmware` 声明 `flashtool_v6`、`flash_preflight` 退回
  warning-only（代价是 V6 断链只能在刷机步暴露，而不是 preflight）。

## 10. VBUS 切电触发模式（`trigger_mode="vbus_cycle"`）运维规程与排障

适用条件：对已关机、无 ADB 的 `MLD_NX2` / `MLD_NX3` 等 MTK 设备（USB 枚举 `0e8d:2046`），
经可控 USB Hub 走 `flash_firmware@1.4.3+` 的 `vbus_cycle` 触发刷机，以及现场换接 Hub
上行口或调整端口线缆后恢复作业。

### 10.1 前置核查与槽位同步

> 警告：操作者不得在 Hub 上行口仍接入 USB 3.0 根集线器（`Bus 002` 下存在 `0bda:0411`
> 伴生 Hub）时启动批量 `vbus_cycle` 刷机。USB 3.0 口上 `uhubctl` 无法切断物理 5V VBUS，
> 设备不会冷重启进入 Preloader，导致 120s 扫口超时失败。

> 警告：物理换插 Agent host 的 USB 主口或拔插设备线缆后，Hub 根路径（如 `1-9` 与 `1-10`）
> 与端口映射会漂移。操作者必须先重新核对实时 sysfs 串号并下发
> `/etc/stp-agent/usb-power-slots.json`；未同步槽位直接触发 PlanRun 会导致 wrapper 拒绝切电
> 或切错端口。

按顺序执行以下步骤：

1. 检查 Agent host 上 USB Hub 是否仅挂载在 USB 2.0 总线：

   ```bash
   lsusb -t
   ```

   预期：`Bus 001` 下存在目标 `Hub` 树，`Bus 002` 下无 `0bda:0411`（`5000M` / `10000M`）
   Hub 节点。
2. 如果 `Bus 002` 下仍存在 `0bda:0411` Hub，停止本流程，联系现场将 Hub 上行线改插至主机
   黑色 USB 2.0 口后再从第 1 步重查。
3. 检查目标 Agent host 的 `uhubctl` 与提权 wrapper 能力：

   ```bash
   which uhubctl && sudo -n /usr/local/sbin/stp-agent-priv selftest && /usr/local/sbin/stp-agent-priv capabilities
   ```

   预期：输出包含 `STP_AGENT_PRIV_SELFTEST_OK`，且能力列表同时包含 `usb-power` 与
   `usb-power/expect-slot`。
4. 如果第 3 步缺少 `usb-power/expect-slot` 或未装 `uhubctl`，停止刷机触发，先通过
   `tools/ansible/playbooks/update_agent.yml`（或安装 `uhubctl` 包）补齐 wrapper 环境。
5. 读取当前接入各子 Hub 端口的设备 USB 串号并生成 `<vars.json>`：

   ```bash
   for d in /sys/bus/usb/devices/1-*.*.*; do
     [ -e "$d/serial" ] && echo "$(basename "$d") $(cat "$d/idVendor"):$(cat "$d/idProduct") $(cat "$d/serial")"
   done
   ```

   预期：所有待刷设备的 `serial` 均可读取，且与控制面设备表一致。
6. 下发槽位映射表到 `/etc/stp-agent/usb-power-slots.json`：

   ```bash
   ansible-playbook -i tools/ansible/inventory.ini \
     tools/ansible/playbooks/ensure_usb_power_slots.yml \
     -e usb_power_hosts=<host> -e @<vars.json>
   ```

   预期：`uhubctl_rc=0`、`wrapper_has_usb_power_expect_slot=True`、`slots=<在位台数>`、
   `failed=0`。
7. 换接新黑色 USB 口或首次投产前，执行单口 12 秒切电取证并观察内核日志（先验证 1 个槽位）：

   ```bash
   sudo -n uhubctl -l <hub_location> -p <port> -a off && sleep 12 && sudo -n uhubctl -l <hub_location> -p <port> -a on
   journalctl -k --since "1 minute ago" --no-pager | grep -E "<gate_port>|idProduct|Preloader|ttyACM"
   ```

   预期：`USB disconnect` 后约 `20–21s`（即上电后 `8–9s`）出现 `idVendor=0e8d, idProduct=2000`
   （`MT65xx Preloader`）及 `ttyACM` 节点。
8. 如果第 7 步在 `USB disconnect` 后 `17–18s` 直接重报 `0e8d:2046` 且设备充电图标未熄灭，
   停止对该口直接放量，按 §10.3 排查主板黑色口差异或设备自身状态。

### 10.2 批量调度节奏与控制面 I/O 护栏

以下各条均为对操作者的要求：

- 同一 Agent host 上每次下发的 `vbus_cycle` PlanRun 必须控制在每批不超过 2 台设备，
  且必须在上一批 PlanRun 到达终态后再触发下一批；同一主机刷机锁串行执行、单台约需 5–6 分钟，
  同 host 堆叠多批会触发 15 分钟 `pending_timeout`。
- 启动批量 VBUS 刷机前，操作者必须先在控制面检查负载与磁盘 I/O（`uptime` 与 `iostat -xz 1 3`），
  不得在控制面运行重型 I/O 扫描（如全量 `start_log_scan.py -m`）导致磁盘队列饱和期间放量；
  控制面存储 I/O 饱和会经 NFS 阻塞远端 `SPFlashToolV6` 写包并拖垮 Agent 心跳（见 #4054）。

### 10.3 实测故障分型、观测命令与止损规则

因 `0e8d:2000` 仅闪现 2–3 秒，排查时操作者建议使用 `journalctl -k` 或 `dmesg -Tw` 查看瞬态枚举，
不得仅凭单次 `lsusb` 下结论：

```bash
journalctl -k --since "10 minutes ago" --no-pager | grep -E "idProduct=2000|0e8d|Preloader|ttyACM"
```

依据 2026-11-18 至 2026-11-19 两批次现场实测，常见失败分型与对应处置如下：

| 内核与现场特征 | 根因判定 | 处置规则 |
|---|---|---|
| `USB disconnect` 后 `17–18s` 直接重报 `0e8d:2046`，`off` 超 45 分钟或切上级父 Hub 口仍 0–1s 回 `2046`，且关机充电图标常亮 / 开机 `off` 仅 ADB 弹窗消失 | 物理 5V VBUS 未断开（主板局部 USB 口或 Hub 仅断 D+/D-）；若拔线按电源键也亮充电图标则为设备自身 PMIC 卡死（`.75` 实测） | 多口集中复现时，操作者必须将 Hub 上行口更换至主机另一个黑色 USB 2.0 口（`.69` 实测换口后 9/9 全过）并按 §10.1 重刷槽位；单台拔线仍亮图标时，操作者可以先重启设备本身后再试 |
| `USB disconnect` 后 `30s`（`12s` settle + `18s` 冷启动）才进入 `0e8d:2046` 或 `201c`，且 `isolate` 后关机无充电图标 | 物理 5V 已真断电，但冷上电错过短暂 Preloader 握手窗口 | 操作者可以对失败设备单点重跑 1 次（`.75` 3 台重跑全过）；若仍直出 `2046`，实测将 `vbus_off_settle_seconds` 调至上限 `30s` 或预断电 `2–16` 分钟均无法稳定拉出 `0e8d:2000`（见 #4063），操作者不得继续盲试 `vbus_cycle`，建议改由现场手动开机连 ADB 后走 `adb_reboot` 刷机 |
| `caught_brom=True`，但写入 `md1img_a` 或 `super` 阶段固定百分比报 `data_mux receive timeout or canceled` | 刷机大分区阶段 USB 传输中断或线缆/设备存储异常 | 连续两次在同一阶段失败时，操作者不得原位继续重跑，建议先更换数据线或倒插到已验证通过的空闲端口再试 |

### 10.4 批次收尾恢复 `0000 off` 端口供电

> 警告：当前 `vbus_cycle` 失败后 `isolate` 会将槽位设为 `0000 off`，且该断电状态对平台不可知、
> 任务结束或取走设备后不会自动恢复（见 #4063）。若遗留 `0000 off` 空端口，后续接入正常设备将
> 无供电且无任何 USB 枚举，极易被误判为端口硬件损坏。

按顺序执行以下步骤：

1. 在已无待刷设备的每台 Agent host 上检查是否存在残留 `0000 off` 端口：

   ```bash
   sudo -n uhubctl | awk '/Current status for hub/{h=$5} /0000 off/{print h ":" $2}'
   ```

   预期：记录所有输出的 `<hub_location>:<port>`；若无任何输出，直接结束本流程。
2. 对第 1 步列出的每个非待排查 `0000 off` 端口恢复供电：

   ```bash
   sudo -n uhubctl -l <hub_location> -p <port> -a on
   ```

   预期：端口状态恢复为 `0100 power`（若接有开机设备则恢复为 `0503 power ...`），且再次执行第 1 步
   检查命令输出为空。
