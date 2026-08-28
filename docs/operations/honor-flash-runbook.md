# Honor 刷机 Runbook（MLD 先行）

> 配套设计：[`docs/design/2026-08-honor-flash-firmware-routing.md`](../design/2026-08-honor-flash-firmware-routing.md)。
> 本文是操作手册：固件上架 → 建计划 → 单台验证 → 放量。

## 0. 前置条件

- `flash_firmware v1.2.0` 已注册（`GET /api/v1/scripts?name=flash_firmware` 能看到 1.2.0 且 is_active）。
- 20 台 Agent host 已 hot-update 到含 v1.2.0 的 code revision。
- 目标固件刷机包已从研发渠道拿到（解压后含 scatter 与 DA 文件）。

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

# 版本指针：之后的刷机默认用这个版本（改这一个文件即切版本）
echo '{"version": "8.0.1.100"}' > "$ROOT/MLD/latest.json"
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
# AGENTS.md「Production access」：.env.backend 的 STP_ADMIN_USER/PASSWORD + AGENT_SECRET
source /home/debian13/stability-test-platform/.env.backend
TOKEN=$(curl -s -H "X-Agent-Secret: $AGENT_SECRET" \
  -F "username=$STP_ADMIN_USER" -F "password=$STP_ADMIN_PASSWORD" \
  http://127.0.0.1:8000/api/v1/auth/token | jq -r .data.access_token)
AUTH="Authorization: Bearer $TOKEN"
```

## 3. 建「MLD 刷机」Plan

init 三步：刷机 → 设备体检 → 重新拿 root（刷机会重置设备，root 必须重做）；
patrol 用 noop 维持心跳。

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/plans -H "$AUTH" \
  -H "Content-Type: application/json" -d '{
  "name": "MLD-刷机-8.0.1.100",
  "description": "Honor MLD 全量刷机（指纹路由 + 同版本跳过 + 刷后核验）",
  "failure_threshold": 0.0,
  "barrier_timeout_seconds": 7200,
  "barrier_max_wait_seconds": 14400,
  "steps": [
    {"step_key": "flash", "script_name": "flash_firmware",
     "script_version": "1.2.0", "stage": "init", "sort_order": 10,
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
  -H "Content-Type: application/json" -d "{\"device_ids\": [$DEVICES], \"note\": \"v1.2.0 首台验证\"}"
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
