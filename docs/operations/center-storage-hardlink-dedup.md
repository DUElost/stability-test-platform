# 中心存储 devices/ 跨 run 硬链接去重（过渡措施，按需执行）

- **状态**：过渡措施，已登记为 `center-storage-interim-dedup`（[`transitions.json`](../governance/transitions.json)），出口是 [ADR-0053](../adr/ADR-0053-center-storage-event-dedup.md) Phase B。届时本手册、脚本与告警描述里的指引一并删除
- **工具**：[`backend/scripts/center_storage_hardlink_dedup.py`](../../backend/scripts/center_storage_hardlink_dedup.py)
- **跟踪**：#3308（ADR-0053 实施跟踪，历次执行结果回填处）、#3233（中心存储容量）

## 什么时候跑

按需执行，不设定时任务。由已有的 host-storage 磁盘水位告警提示，只对中心存储挂载点（`/mnt/stp-aee`）有意义：

| 告警 | 含义 | 动作 |
|---|---|---|
| `StabilityHostFilesystemFillingUp` | 剩余不足一半，且 72 小时内会写满 | 跑一次去重 |
| `StabilityHostFilesystemLowSpace` | 剩余不足 20% | 跑一次去重，再看趋势是否缓解 |
| `StabilityHostFilesystemCritical` | 剩余不足 10% | 立即跑；仍不够再按 #3233 清理可再生的提取副本 |

去重只回收**结束超过 24 小时**的 run 之间的重复；刚结束的 run 要过一天才会被纳入。

## 它做什么，不做什么

- 把 `devices/` 下跨 run **字节相同**的文件改成硬链接：每个 run 的目录与文件原样都在，只是共用磁盘块，**不删除任何证据**；retention 删某个 run 只去掉它那一个链接。
- 只处理已终态（SUCCESS / PARTIAL_SUCCESS / FAILED）且结束超过 24 小时的 run，运行中的 run 不碰。
- 只处理 ≥ 64 KiB、mtime 早于 24 小时的普通文件；大小、首尾 64 KiB 快速哈希、全量 SHA-256 三级都相同才链接。
- 不碰 `jira/`：厂商 Jira 工具是否会原地改文件尚未证实（ADR-0053 D5）。脚本拒绝 `devices` 以外的根目录。

## 步骤

在发布根下执行（`venv` 与 `backend` 包都在这里）：

```bash
cd /home/debian13/stp-releases/current
set -a && . ./.env.backend && set +a   # --eligible-from-db 要 DATABASE_URL；脚本用只读连接查库
D=~/stp-ops/$(date +%F)-dedup && mkdir -p "$D"
df -B1 --output=avail /mnt/stp-aee | tail -1 > "$D/avail_before.txt"

# 1) 预演：只统计，不改动
venv/bin/python -m backend.scripts.center_storage_hardlink_dedup --eligible-from-db 2> "$D/dry_summary.txt"
cat "$D/dry_summary.txt"   # == DRY-RUN: runs=… dup_groups=… files_linked=… freed=… GB

# 2) 执行：IO 优先级设为 idle，给生产写入让路
ionice -c3 nice -n10 venv/bin/python -m backend.scripts.center_storage_hardlink_dedup \
  --eligible-from-db --execute > "$D/linked.log" 2> "$D/summary.txt"
cat "$D/summary.txt"       # == EXECUTED: …
```

耗时主要花在全量哈希，约 110 MB/s。2026-09-25 首次全量：527 GB 候选用时约 72 分钟，释放 389.1 GB。之后每次主要处理新结束 run 的重复，会快得多。

## 核验

```bash
df -B1 --output=avail /mnt/stp-aee | tail -1                 # 与 avail_before 对比
find /mnt/stp-aee/devices -name '*.stp-dedup-tmp' | wc -l    # 残留临时链接，应为 0
shuf -n 20 "$D/linked.log" | awk -F'\t' '{print $2"\t"$4}' | while IFS=$'\t' read -r p c; do
  [ "$(stat -c %i "$p")" = "$(stat -c %i "$c")" ] || echo "MISMATCH $p"
done                                                          # 抽样同 inode：无输出即通过
```

把运行时间、`summary.txt` 的汇总行和可用空间前后值回填到 #3308。

## 中断与重跑

可以随时中断后重跑：已经共用 inode 的文件自动跳过，上次残留的临时链接会在执行模式下先清理。扫描之后被改动过的文件会被跳过（计入 `skipped`），不会冒险替换。

## 为什么只是过渡

共享 inode 的安全依赖上送路径的约定：复制 → 校验 → REMOTE 后不再修改，重传先删除整个目录，回收与 unassigned 归属只做 unlink / rename。这与 ADR-0053 D2「不能仅靠约定保护共享 inode」相悖，所以登记为过渡项。ADR-0053 Phase B 把读写切到内容寻址的对象存储后，本手册、脚本与过渡项一并删除。
