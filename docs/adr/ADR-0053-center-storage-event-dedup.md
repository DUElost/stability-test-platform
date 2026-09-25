# ADR-0053：中心存储的事件按内容只存一份——跨 run 硬链接去重与 baseline 免重复拉取

- 状态：**Proposed** v0.1（2026-09-25 起草，待 owner 裁决）
- 优先级：P1（存储即将写满的止血已由 L0 与磁盘水位告警承担，见 §6；本 ADR 是写入侧的根治）
- 目标里程碑：M7
- 日期：2026-09-25
- 决策者：owner（待裁决）；起草：平台研发组
- 标签：center-storage, device-log-event, baseline, dedup, hardlink, capacity
- 归属域：semantic-ownership center-storage-model
- 关联：[#3233](https://github.com/DUElost/stability-test-platform/issues/3233)（中心存储写满风险与告警）/
  [#3230](https://github.com/DUElost/stability-test-platform/issues/3230)（台账；复审 C2：生产保留期停用）/
  [ADR-0025](./ADR-0025-phase4-architecture-alignment.md) D4（中心存储模型，本稿修订其「中心只收有效子集」这一前提的实现方式）/
  [ADR-0028](./ADR-0028-device-log-event-and-continuous-upload.md)（DLE 唯一权威与持续上送，本稿继承其全部语义，只改存放方式）
- 版本记录：v0.1（2026-09-25）首次提出，D1–D6 待裁决；D4（baseline 免拉取）被设计为可单独延后

## 1. 背景（2026-09-25 只读实测）

| 事实 | 出处 / 口径 |
|---|---|
| 中心存储 `/mnt/stp-aee`（sda1，916G）7 天从 46% 涨到 88%，平均每天约 59 GB；owner 当日删除 134 GB 可再生提取副本后降到 75% | Prometheus `node_filesystem_avail_bytes` 7 天历史 |
| 写入来源：`devices/` 每天 17–55 GB（事件目录），`jira/` 7 天 143 GB（提取副本） | 按文件 mtime 分日统计 |
| `devices/` 下 1180 个不同事件目录名共存了 2955 份，最多一个存了 11 份；按「目录名 + 大小」估算约 50%（259.5 GB）是跨 run 重复 | 只读扫描；**目录名不保证跨设备唯一**（#1073），内容级数字以 §6 的 L0 实测为准 |
| 机制一：`AeeReconciler._run_baseline_snapshot` 在每个 job 首轮用 **job 级**前缀 `watcher_baseline:{job_id}` 补拉设备 `/data/aee_exp` 的全部现存转储——有意设计，理由是「否则设备历史问题会被静默吞掉」 | `backend/agent/aee/reconciler.py` |
| 机制二：`EventUploader` 把本地事件目录整体复制到 `devices/{run}/{event_id}/{basename}`，校验整目录 checksum 后回写 REMOTE，此后不再原地修改；重传先 `rmtree` 再复制 | `backend/agent/event_uploader.py`、`upload_manager._copytree_safe` |
| 结果：只要历史转储还留在设备上，**每个新 run 都重新 adb 拉取一遍、再在中心存一整份** | 上两行合成 |
| 统计口径：watcher summary 按 `entry_origin` 分别统计 baseline 与 runtime，主看板的崩溃数没有被历史事件抬高；scan / merge / 提单是否同样区分**待核** | `backend/services/plan_run_watcher_summary.py` |
| 提取：`dedup_extract` 从 `devices/` 整目录**复制**到 `jira/{run}/`，是第二份副本 | `backend/services/dedup_extract.py` |
| 回收：保留清理是 `devices|dedup|jira|_meta/{run}` 的唯一回收路径；生产 `PLAN_RUN_RETENTION_DAYS=36500`（owner 确认有意保留全部历史），所以不回收 | `backend/scheduler/cron_scheduler.py`、过渡台账 `plan-run-retention-disabled` |
| 写入通道：Agent 经 NFS（`/etc/exports.d/stp-aee.exports`，rw + root_squash）以 debian13 身份写入，`devices/` 全部文件属于同一用户、同一文件系统 | 只读核对 |

原模型的前提已经变了。ADR-0025 D4 设定中心只收「有效子集」：汇总报告、报告引用的事件目录、溢出目录。
ADR-0028 为修 FAILED 运行事件永不上送等缺口，改为持续上送全部事件。baseline 补拉又让这个全集按 run 数倍增，
而字节层面从未去重。

## 2. 决策（待裁决）

- **D1 语义不变**：每个 run 仍拥有自己的事件目录（`devices/{run}/{event_id}/…`）和自己的 DLE 行；
  「行是目录的唯一索引」、按 run 回收、下载与提取的路径都不变。本 ADR 只改变**字节的存放方式**。
- **D2 同内容只存一份**：内容相同的事件文件，在中心以**硬链接**呈现到各 run 的目录，不再复制。
  最后一个链接被删除时空间才释放，按 run 回收的语义因此自然成立。
- **D3 上送侧链接**：`EventUploader` 复制前先按 `(serial, checksum)` 查找已 REMOTE 的同内容副本（控制面只读查询，
  或 Agent 本地索引）。命中则逐文件 `os.link` 到目标目录，再走既有的 checksum 校验并回写 REMOTE；
  任何失败（`EXDEV`、`EPERM`、源缺失、校验不符）都回退到现有的整目录复制。
- **D4 baseline 免重复拉取**（可单独延后）：对同一设备、同一 db_history 行 `(serial, aee_type, line)` 已经上送
  且源仍在的 baseline 事件，不再 adb 拉取，直接按 D3 为本 run 建目录链接和 DLE（`entry_origin=baseline` 不变）。
  身份用 db_history 行而不是目录名（#1073）。在 3750 台规模下，这同时省下 USB/ADB 带宽和 Agent HDD 写入（关联 #3219）。
- **D5 提取暂不链接**：`jira/` 继续整目录复制，直到实测证明厂商 Jira 工具不会原地修改提取包（`jira/` 指纹快照对比，
  2026-09-25 已建基线）。在此之前，提取包体量按「提单完成或运行结束 M 天后清理可再生副本」控制，与分层保留一并裁决。
- **D6 可观测**：上送回执带「链接 / 复制」字节数，控制面出每个 run 的去重率；中心存储按族（devices / jira / dedup）的
  增长进入指标。趋势告警的回测参数在 D3 上线后按新写入速率复核。

## 3. 备选方案与权衡

| 方案 | 为什么不选 |
|---|---|
| A. 只恢复有限保留期 | 删掉数据库历史，与 owner「保留全部历史」冲突；也不解决 run 之间的倍增 |
| B. baseline 只存首份，后续 run 的 DLE 直接指向首个 run 的 `remote_path` | 省同样空间，但跨 run 引用打破「行是目录的唯一索引」：首个 run 被回收后，引用它的 run 证据悬空 |
| C. 取消 baseline 补拉 | 设备历史问题不可见，原设计明确拒绝 |
| D. 内容寻址存储（`devices/_cas/{sha}` + 每 run 清单） | 最彻底，但改变目录布局，下载、提取、保留全要改；硬链接在文件系统层实现同样效果、布局零变化 |
| E. 用符号链接 | 下载打包拒绝软链（`plan_run_artifact_download` 的 `is_symlink` 判据），且删除源之后链接悬空 |

## 4. 影响

- **存储**：存量由 L0 按同一原理处理（§6）；增量写入预计降低约一半，以实施后实测为准。
- **约束**：`devices/` 必须在同一个文件系统上（当前是 sda1 单卷）；Agent 在 NFS 上创建硬链接需要落在同一个 export 内。
  Phase A 开工前先在一台 host 上实测 NFS `link()`。
- **风险**：硬链接共享 inode，任何「原地改写」都会波及所有 run。已核实上送流程是「复制 → 校验 → REMOTE」，之后不再修改；
  重传先 `rmtree`（unlink 不影响其它链接）。今后凡是写入 `devices/` 的代码，都必须遵守「只新增文件、不原地改」，列入代码评审清单。
- **统计**：不变。DLE 行与 `entry_origin` 语义都不变。

## 5. 落地与验收

1. **Phase 0（已做）**：L0 存量去重脚本（运维动作，owner 执行，只处理已结束超过 24 小时的 run，逐文件全量哈希判重）
   与磁盘水位告警（PR #3272）。
2. **Phase A（D3）**：Agent 上送侧链接，先一台 host 灰度再全量。验收：
   - 同内容事件第二次上送时中心新增 0 字节；
   - checksum 一致；
   - 回退路径（`EXDEV` / 源缺失）有测试覆盖。
3. **Phase B（D4）**：baseline 免拉取。验收：新 run 的 baseline 事件不再触发 adb 拉取，DLE、看板、提取结果与改前一致。
4. **与分层保留的关系**：本 ADR 只减少字节，不改变回收策略。分层保留（数据库事实长期保留、原始证据 N 天、派生物 M 天）
   另立 ADR（#3230 G4），是过渡项 `plan-run-retention-disabled` 的出口；两者都落地后再按实测定扩容。

## 6. 证据与待回填

- L0 抽样 dry-run（5 个已知重复的 run）与全量执行的逐文件实测数字：待回填。
- `jira/` 指纹对比（厂商工具是否原地改文件）：待回填，决定 D5。
- scan / merge / 提单是否区分 baseline：待核，结论并入 D4 的验收口径。
