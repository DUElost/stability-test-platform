# DLE 上送标记被 timestamp fallback 目录名静默打断（P1 修复）

Status: implemented
Class: bug-fix

## Decision

2026-08-30 日志流转 E2E 验证（run 268）发现：db_history 行 timestamp 无法
解析时（本次注入 epoch 数字触发），`format_timestamp_for_filename` 的
fallback 用 `strftime("%Y_%m%d_%H%M%S_%f")[:21]` 生成 **4-6 位**末段目录名
（如 `2026_0830_070527_9924_...`），而 `event_dirs._EVENT_DIR_BASENAME_RE`
只认 **3 位**（`\d{3}`）——`event_dir_basename_from_path` 提取空 →
`upload_task` 按 xls Path 列 basename 匹配标记 UPLOAD_PENDING 时
marked=0 → **DLE 永远停留 LOCAL，不上送、无任何报错**（静默断裂）。

修复两处（双保险）：

1. `timestamp.py` fallback：固定 `_000` 3 位毫秒——fallback 输出与正常
   路径格式完全一致（根治）。
2. `event_dirs.py` 正则：`\d{3}` → `\d{3,6}`——防御历史已产生的 4-6 位
   目录名（兼容，不误伤 3 位）。

涉及文件：`backend/agent/aee/timestamp.py` / `backend/agent/aee/event_dirs.py`
（后者同时被控制面 `dedup_extract.py` import——两边都要部署）。

## Alternatives

- **只改正则**：历史 4 位目录可识别，但 fallback 仍持续产出非常规格式，
  未来 7/8 位等更多漂移还要再放宽——治标。
- **只改 fallback**：历史已产生的 4 位目录（run 268 的 HDD 残留）无法被
  重新 scan 识别——治标不治本。
- 两处都改：根治 + 兼容历史，代价是两个小改动。

## Verification

- 单测：test_event_dirs.py 新增 4/6 位用例（含 run 268 实证路径形态）；
  test_aee_timestamp_tz.py 新增 fallback 3 位断言
- 全量 agent 测试 1265 passed；ruff clean
- 真机前置证据：run 268（4 位名 → marked=0 断链）vs run 270（3 位名 →
  marked=1 → REMOTE → ARCHIVED 全通）

## Revisit

- 若设备侧出现非 `%Y-%m-%d %H:%M:%S[.%f]` 之外的新 timestamp 格式，
  parse_timestamp 需同步扩展（fallback 只是兜底，不该成为常态路径）。
- run 268 残留的 4 位 HDD 目录已消费（DLE LOCAL 记录在案），不重扫；
  未来 scan 新 run 时若遇历史 4 位目录，正则已兼容。
