# watcher 落地名的 13 位 epoch 前缀进事件目录识别（#2822，inotifyd 兜底断链）

Status: implemented
Class: bug-fix

## Decision

取票面方向 1（**识别侧放宽**），不取方向 2（puller 命名归一）：

- `is_event_dir_basename` 对 `<13位epoch_ms>_<原名>` 形态**剥离前缀后让剩余段
  自己再过一遍原判据**——不是「13 位数打头就放行」，无关目录不会被误收
  （三条反例断言钉住：12 位/14 位粘连/随机尾巴都不认）；
- `event_dir_basename_from_path` **仍返回带前缀全名**：它是标记 SQL 与
  DLE.remote_path、scan xls Path 列的共同匹配键——识别放宽 ≠ 返回键改写，
  剥了才真断链；
- puller 侧不改名：`_compose_local_path` 的 epoch 前缀是防同名冲突的
  结构化命名（reconciler 主路不落地该形态），改命名契约要动 staging 消费方，
  收益不比识别侧放宽干净。

判据教训挂名：**#389（正则过严静默断 DLE 上送标记链）的同型复发**——这次
断的是 inotifyd 兜底路（reconciler 下线场景），全生产史 inotifyd 源 DLE 在
#310 E2E 前为 0 所以无人踩到。修复带 watcher 落地式样的**实证样本回归**
（`1789826505754_2026_0827_221918_553_db.01.ANR`，#310 E2E host .92 实录）。

## Alternatives

- **puller 去前缀对齐 reconciler**（票方向 2）：弃——改名触既有 staging 消费方
  兼容评估，且历史 LOCAL 存量仍带前缀，放宽识别反正要做，做了识别改名无增益；
- **在 from_path 里返回剥前缀名**：弃——匹配键改写=把断链从正则侧搬到 SQL 侧。

## Verification

- agent 套件全量 → **2229 passed**（`is_event_dir_basename` 消费面宽，全量兜）；
- 新用例 3 条（识别/带前缀返回/反例不放宽）+ `test_event_dirs.py` 全绿；
- 控制面消费链 `test_dedup_extract.py` → 28 passed；ruff clean；
- `check:quick` → 见 PR。

## Revisit

- 常态无感（reconciler 是主路）：本修复的实弹验收要等一次 inotifyd-only 场景
  （#310 执行程序可复跑）；DLE 上送标记命中与否有 `upload_task` 日志
  （saq_upload_*）可查；
- 幽灵 OFFLINE 行清理（#2802 评论第 4 条顺带项）不属本单面。
