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

1. flash 步骤 `metrics.route.decided_by == "fingerprint"`，`family == "MLD"`，
   `version` == manifest 版本。
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
- **UNISOC（Z258）不适用**：SP Flash Tool 只覆盖 MTK；Z258 刷机另行立项。
- **吞吐上限 = 每 host 同时 1 台**：需要并行时按 USB 控制器分片锁是后续课题
  （设计文档 §8 重议触发）。

## 7. V6 代际机型（535 / MLD_NX2）

适用条件：目标机型的固件包由 SP Flash Tool **V6** 驱动（`download_agent/flash.xml` 形态），
例如 MLD_NX2（板级 P535AA / MT6835）。本节替换 §1 的上架动作；§2–§5 的建计划、单台验证
与放量流程不变。

> 警告（刷机窗口前必查，2026-10-12 实测）：NX2 所在 host（`172.21.15.83–86`，Debian 13）
> **未装 V6 的运行库**——`ldd SPFlashToolV6` 报 `libGL.so.1 => not found`，工具连 `-h` 都跑不起来。
> 补包属 provisioning：`apt-get install -y --no-install-recommends libgl1 libglx0 libglvnd0`
> （即 `agent_flash_prereq_packages_v6`，KRB5 面这 4 台已具备）。不补包时刷机步会在拿锁之前
> 以 `environment precheck failed: shared-libs: missing: libGL.so.1` 硬失败。

> 警告（混跑风险）：`flash_firmware` 的初始门控把 `0e8d:201c` 当刷机态隐藏，而实测
> `201c` 是 MLD-LX2/LX3/NX2 的**正常 adb 态** → 同 host 有其他测试在跑时，一次刷机会把邻机
> 一起摘线（判据问题见 #3690）。V6 单台验证请选择**无其他在跑作业**的 host 与时间窗。

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

   预期（NX2 实测值，设备 `ATHTNX6930000085` @ `172.21.15.83`）：model = `MLD-NX2`
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
5. 更新族级版本指针（同族多机型并存，per-model 映射；不要整体覆盖别人的键）：

   ```bash
   cat /mnt/stp-aee/firmware/MLD/latest.json
   ```

   预期：改后仍保留 `MLD_LX2` / `MLD_LX3` 键，并新增
   `"MLD_NX2": "<version>"`（`MLD-NX2` 连字符拼写由脚本自动互转，二选一即可）。
   写入方式必须「临时文件 + `os.replace`」并 `chmod 0644`——CIFS 上以 0600 落盘的指针文件
   会让 Agent 侧读不到 `latest.json`（本手册 2026-10-12 踩过一次）。
6. scan 注册脚本新版本，再重指或新建 Plan（顺序不可颠倒，见上方警告）。

   ```bash
   curl -s -X POST "$BASE/api/v1/scripts/scan" -H "Authorization: Bearer $TOKEN"
   ```

   预期：响应 `created` 含 `flash_firmware@1.3.19` / `flash_preflight@1.0.7`，
   `conflicts` 为空。随后按 §3 建计划（`script:flash_preflight` + `script:flash_firmware`
   两步都指向新版本），并按 §4 单台验证。
7. 单台验证时核对四个 V6 判据（任一不符即回到设计文档 §6，不得放量）：
   - `step_trace` 的 `metrics.route.tool_generation` 为 `v6`；
   - `metrics.command_argv` 是 `[... , "-c", "firmware-upgrade", "-f", "<...>/download_agent/flash.xml", "-b", "auto"]`，
     且不含 `-d` / `-s`；
   - `metrics.post_flash_verify` 的版本回读与 manifest 一致；
   - 刷机窗口内出现过的 MTK pid（判据是 `_FLASH_STAGE_PIDS` 是否覆盖该机型）：

     ```bash
     lsusb -d 0e8d: | awk '{print $6}'
     ```

   预期：窗口内出现的 pid 全在 `0003/2000/2001/201c/2026/3000` 内。出现新 pid 时必须先发
   新版本补集合再放量——集合外的刷机态口不会被门控压制，工具可能抓到邻机。
   **别把这条读成「`201c` 该被隐藏」**：2026-10-12 实测 `201c` 是 MLD-LX2/LX3/NX2 的正常
   adb 在线态（正常态另有 `2046`），初始门控连它一起隐藏正是 #3690 要裁决的判据问题。
