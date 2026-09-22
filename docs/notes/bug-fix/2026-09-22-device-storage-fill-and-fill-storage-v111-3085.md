# 设备满盘（not enough space）三源累积与 fill_storage v1.1.1 双向调节（#3085）

Status: proposed
Class: bug-fix

## Decision

**结论一：24 台慢性 `pm install: not enough space`（近 12 窗 88 条、无自愈）是三类「测试期碎片无回收」的累积，不是设备/固件问题。**

| 设备 | 分区空闲 | 主占用（2026-09-22 root 实读） |
|---|---|---|
| `62002360`(.59，**12/12 窗全中**) | 129 MB（100%） | `/data/corefile` **86.6 GB**（614×`core-crash_dump64-*`，属主 `nfc`/`wifi`） |
| `6R0A77SS****0117`(.76) | 267 MB | `/data/local/tmp/fill.bin` **45.5 GB** + `/data/ylog/ap` 44.1 GB |
| `6R0A77SS****0091`(.76) | 448 MB | `/data/media/0/Download` 47.3 GB + `…/Android` 13 GB + ylog 27.5 GB |
| `6R0A57SS****0320`(.75) | 342 MB | `/data/ylog/ap` **81.5 GB** + fill.bin 14.9 GB |

止血已执行（本单记录）：`/data/corefile/*` 白名单清理 **12 台回收 ~1.06 TB**；低风险白名单（`fill.bin`/`stp_ui.xml`/`fastbot--running-*`/`FillTest`/`sd_logs`）**4 台回收 ~66.6 GB**；4 台 OFFLINE 未触达。

**结论二：`fill_storage`（v1.0.0–v1.1.0）的语义缺陷 = 「单向 + 基线含自建文件」。**
`need = target_used - used_kb` 用的是 `df` 原值（**含 fill 文件自身**），且 `need<=0` 直接 `skipped+already_met`——旧目标（60%）留下的数十 GB 文件让后续「填到 40%」永远短路，文件永不回收、每步假绿（实测 45.5 GB 单文件即此机制）。

**结论三（本 PR）：v1.1.1 把「填到目标百分比」做成双向调节。**
`base_used = used_kb - fill_kb`（剔除自建文件）→ `need = target_used - base_used`：
- `need<=0` 且无自建文件 → 维持 `skipped+already_met`；
- `need<=0` 且有自建文件 → **释放**（`rm -f <fill_path>`，只碰参数指定路径）；
- `need>0` → `dd` **覆盖写**到恰好 `need`（同目标幂等；目标调低即缩容）。
metrics 增 `mode`（`filled`/`released`/`already_met`）与 `base_pct`；沿用 v1.0.2 的向上取整与绝对量核验、v1.1.0 的 `progress_heartbeat`。

## Alternatives

- **只做设备侧清理、不改脚本**：否决——文件会被后续 fill 步骤再次留下（且历史残留不因清理消失），且「目标调低不缩容」的语义错误仍在。
- **把释放改成 `truncate -s 0`（保留空文件）**：等价但更弱——空文件无用途；按「白名单只碰自建路径」直接 `rm -f` 更干净，也与 gpu_finish v1.0.6 的回收先例一致。
- **把 `fill_path` 固定为「每次运行先清空」**：语义错——步骤目标是「占用率到 target%」，不是「清空」；先清后写会在「目标低于现状」时把盘瞬间清空再填，徒增写入。
- **在脚本里顺带删其它历史残留（corefile/ylog）**：越界——脚本只应管理自建路径；那两类属设备/厂商面（本单其它条目跟进）。

## Verification

- `backend/agent/tests/test_fill_storage_scripts.py`（**新建**，此前该族零测试）**9 例全绿**：低占用填到目标（dd 块数=ceil(need/bs)、回读核验）/ 回读不足 fail 带 evidence / dd 失败带 rc+stderr / **旧残留释放**（used 90% 含自建 60% 且 base=30% ≥ 目标 25% → `rm -f`、不 dd）/ 无自建文件时 already_met 不动文件 / 释放失败带 rc / **同目标幂等**（base 恰为目标 → 释放，`base_pct` 正确）/ **目标调低缩容**（60%→40%：dd 覆盖写 count=10，文件从 60% 缩到 10%）/ **v1.1.0 对照锚点**（高占用即短路、不 rm 不 dd 不探测）。
- **变异检查**：① 基线不剔除自建文件（`base_used = used_kb`）→ 幂等与缩容 2 例立刻红；② 去掉释放路径（恢复短路）→ 3 例立刻红；两处恢复后 9 passed。
- 门禁：`check:quick` 全绿；`tools/dev/check-script-version-immutability.py --base origin/main` → OK（v1.1.0 及更早未原地改动）。

## Revisit

- **步骤若重新被某个 Plan 引用**：v1.1.1 首次运行会把历史残留下的 `fill.bin` 一次性释放/缩容（幂等，无需人工介入）。
- **部署面**：当前无任何 `plan_step` 引用 `fill_storage` ⇒ 上线只需 `scan` 注册（无需 `plan_step` 重指）；若后续有 Plan 使用，按脚本版本上线五步执行。
- **本单其余三条（未完成）**：① ylog 轮转（厂商/设备面）；② core dump 关闭或保留期；③ 满盘监测形态（复用步骤采集 `data_free_mb` 或从失败报文派生告警，**不做** 500 台独立 adb 轮询）。
- 4 台 OFFLINE 设备（自 09-20）上线后按 `corefile_cleanup.py` 补跑。
