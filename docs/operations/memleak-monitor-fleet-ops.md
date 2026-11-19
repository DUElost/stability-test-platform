# 内存泄漏监测工具的机队运维经验（Agent 主机侧 hprof 监测）

> **最后更新**：2026-11-19
> **适用对象**：`automation-toolkit` 的 `python-tools/stability_Memory-Leak-Monitor`（下称「监测工具」）在 Agent 主机上的批量下发、窗口收口与只读巡检。
> **本文不定义判据**：档位、阈值、触发路径、版本归档纪律的权威是模块文档
> [`automation-toolkit/python-tools/stability_Memory-Leak-Monitor/readme.txt`](https://github.com/DUElost/automation-toolkit/blob/main/python-tools/stability_Memory-Leak-Monitor/readme.txt)。
> 本文只写怎么安全地操作它，以及怎么取它的数才不会取错。

## 1. 适用条件与边界

在下列情况下使用本文：

- Owner 要求把监测工具的一个新版本下发到多台 Agent 主机，或要求在指定时刻收口窗口；
- 值班者要回答「监测是否还在跑、覆盖多少设备、产物是否落到中心存储」；
- 复盘者要从中心存储（外号 15.4）取堆曲线或 hprof 作为归因证据。

以下三项不归本文管，执行者不得在本文里找它们的判据：

| 事项 | 权威所在 |
|---|---|
| 监测判据、版本锚点、归档纪律 | 监测工具模块 `readme.txt`（automation-toolkit 仓） |
| Agent 自身的版本门禁与热更新 | [`agent-version-and-hot-update.md`](./agent-version-and-hot-update.md)。监测工具不是 Agent 发布单元，不走 `script-version-lifecycle`，也不进 `tool_manifest.json` |
| 设备从 adb 消失的分层判别 | [`host-device-visibility-triage.md`](./host-device-visibility-triage.md)。本文 §7 只记录与该判别一致的结论，不重复其判据 |

## 2. 名词与路径（先对齐，再操作）

| 本文用词 | 指什么 | 现网取值（2026-11-19 实测） |
|---|---|---|
| 中心存储 | 存储角色 4（别名 CIFS / NFS / sonic_tinno），见 [`2026-storage-roles-and-aliases.md`](../design/2026-storage-roles-and-aliases.md) §4.4 | UNC `//172.21.x.x/jxtinno/sonic_tinno`，Agent 主机挂载点 `/home/android/sonic_agent/logs/ftp_log/sonic_tinno` |
| 「15.4」 | 中心存储的**外号**，不得当成「现网 UNC 已在 15.4」的事实陈述 | 同上，写文档时以实际 `mount` 输出为准 |
| 主机脚本目录 | 监测工具的落盘位置，一机一份 | `/home/android/memleak/`（`memleak_monitor_2d.py`、`run_2d.sh`、`stop_2d.sh`、`watchdog.sh`、`hot_devices.txt`、`deadline_epoch`、`started_at`、`logs/`） |
| 本地暂存 | hprof 先落主机盘再镜像中心存储的那份副本 | `/home/android/memleak_local/<战役目录>/<串号>/leak_*.hprof` |
| 战役目录 | 按 build + 日期分组的一条产物链的根 | 命名 `MLD-<机型>_16_<buildtoken>_<YYMMDD>_Systeminfo`，**日期段是 `%y%m%d`（例 `261119`），不是 `20261119`** |
| 堆曲线 | 三个文件对应三类目标，文件名不带进程名 | `<战役目录>/<串号>/` 下 `meminfo_heap.txt`（system_server）、`meminfo_heap_launcher.txt`（launcher3）、`meminfo_heap_apps.txt`（systemui/settings，多进程合写、含 `proc=`） |
| 监测日志 | 进程启动当天定名，进程不重启就不换名 | `/home/android/memleak/logs/MonkeyTest_<YYYY_MM_DD>.log`；同目录 `watchdog.log` 是看门狗心跳（300s 一跳） |

## 3. 生命周期门：五道门各自独立

Owner 与执行者必须把下面五件事当五个独立授权点，前一门的完成不构成后一门的许可：

1. **入库**：实施者把新版本提交到分支并开 PR。
2. **合并**：Owner 决定。PR 合并只改变仓库 `main` 的字节，不改变任何主机上的字节。
3. **下发**：执行者把 `main` 的文件写到 Agent 主机。文件写入本身不重启进程，因此**不得**把「已下发文件」表述为「已生效」。
4. **生效**：执行者重启监测进程。重启会使档位状态、批次计时与当日重复抓取状态清零，泄漏观测从 0 重计（模块 readme 已知限制 6、8）。
5. **验收**：执行者逐台回读版本字节、进程、窗口终点、心跳与产物，才可以说「fleet 在跑 vX」。

反例（2026-11-16 至 11-18 实际发生过）：`main` 已是 v11，20 台主机仍在跑 v10，持续两天；期间任何「已经用上新版本」的结论都是错的。执行者报告版本状态时必须给出**逐台 sha 回读**，不得引用仓库状态。

## 4. SOP A：把一个新版本下发到 fleet 并续窗

适用条件：Owner 已合并目标版本到 `main`，并明确授权下发与新的窗口终点。

> **警告**：本流程会重启监测进程。重启清零档位状态、批次计时与当日重复抓取状态，泄漏观测从 0 重计；不得在观测窗口中途随意执行。
> **警告**：`stop_2d.sh` 会让监测进程走 SIGTERM 收尾，收尾包含「停设备上的 monkey + 删设备侧资源 + 恢复息屏超时」。若该主机的进程环境里 `MEMLEAK_MONKEY=1`，本流程会真的动设备；因此第 3 步是硬性预检，不得跳过。

按顺序执行以下步骤：

1. 向 Owner 取一个**绝对终点 epoch**（例：`date -d '2026-11-22 18:00' +%s`）。
   执行者不得用「+48 小时」这类相对值：批次串行会拖几十分钟，相对值会让各机终点不齐。
2. 从 `main` 取待下发文件到本机暂存目录，不得切换或改写他人的共享检出：
   ```bash
   git -C <automation-toolkit> show origin/main:python-tools/stability_Memory-Leak-Monitor/memleak_monitor_2d.py > stage/memleak_monitor_2d.py
   ```
   预期：`sha256sum stage/* | cut -c1-8` 与模块 readme 的版本锚点逐字一致。
3. 逐台复核在跑进程的 monkey 开关（查法：读 `/proc/<pid>/environ`，不读配置文件）：
   ```bash
   P=$(ps -C python3 -o pid=,cmd= | grep '[m]emleak_monitor_2d.py' | awk '{print $1}')
   tr '\0' '\n' < /proc/$P/environ | grep '^MEMLEAK_MONKEY='
   ```
   预期：无输出（默认停用）。若出现 `MEMLEAK_MONKEY=1`，执行者必须停下本流程，由 Owner 决定「是否停设备上的 monkey」，未得答复前不得继续。
4. 确认当前窗口状态并留档（查法：`cat /home/android/memleak/deadline_epoch /home/android/memleak/started_at`）。
5. 停：`ssh <host> 'bash /home/android/memleak/stop_2d.sh'`。
   预期：输出含 `MONITOR_EXITED`；随后 `ps -C python3 -o cmd --no-headers | grep -c '[m]emleak_monitor_2d.py'` 为 `0`。
6. 备份再覆盖：先 `cp -a memleak_monitor_2d.py memleak_monitor_2d.py.bak-<ts>-<旧版>`，再写新文件。
   Agent 主机上这些文件属 `android` 可写，执行者**不得**用 `sudo` 覆盖（会留下 root 属主副本，后续会话无法再改）。
   预期：`ls` 里出现 `.bak-<ts>-v<旧>`；`sha256sum` 等于版本锚点。
7. 起新窗口（终点用绝对 epoch 现算）：
   ```bash
   DURATION=$(( <终点epoch> - $(date +%s) )) bash /home/android/memleak/run_2d.sh
   ```
   预期：输出 `STARTED pid=… deadline=<ISO>`，且 `deadline_epoch` 文件等于终点；若打印 `ALREADY_RUNNING` 或 `ABORT: 15.4 cifs 未挂载`（脚本原文，其中「15.4」是中心存储外号），本步未生效，回到第 5 步排查。
8. 等首轮心跳（≤150s）后逐台验收：版本 sha、`deadline_epoch`、`started_at`、`logs/MonkeyTest_<当天>.log` 的 mtime 龄 ≤5 分钟、`本轮检查完成` 行数 ≥1、`[ERROR]` 数。
9. 全部主机完成后，把逐台结果落成一份表格归档（本文 §8 的形式），执行者不得只报「已下发完成」。

批次建议：串行下发、一台验过再下一台。实测每台约 2 分钟，20 台尾部主机会空窗 30 分钟以上——Owner 授权下发时应同时确认这个空窗长度，或改分多轮。

## 5. SOP B：窗口收口与停止监测

适用条件：窗口到点，或 Owner 要求立即停止监测。

> **警告**：到点自停是正常路径，执行者到此只做证据固化；`watchdog.sh` 的存活条件是 `deadline_epoch + 3600`，且它**不重启**监测进程，因此执行者不得为了「让监测继续」去杀看门狗或改它的时钟。

按顺序执行以下步骤：

1. 判断终态：`pgrep -f '[m]emleak_monitor'` 与 `cat /home/android/memleak/deadline_epoch`。
   到点且进程已退出 → 走第 3 步；未到点但 Owner 要求停止 → 走第 2 步。
2. 停止（先做 §4 第 3 步的 monkey 预检）：`bash /home/android/memleak/stop_2d.sh`。
   预期：`MONITOR_EXITED`，`STOP_REQUESTED` 文件出现。该文件是「人为停止」的标记，`run_2d.sh` 会在下次启动时自行清除，执行者不得手工删除来「假装恢复」。
3. 固化证据（停止后必做，否则事后无法区分「没抓到」与「没在跑」）：
   - 每台 `ps -C python3` 计数、`pgrep -fc '[w]atchdog[.]sh'`、`run.pid`/`watchdog.pid`、`deadline_epoch` 是否仍为原值；
   - 最后一行监测日志与 `watchdog.log` 的时刻；
   - 本窗口的产物总账（见 §6 口径）。
4. 复核中断损伤：
   ```bash
   find /home/android/memleak_local -name 'leak_*.hprof' -size -1M
   find /home/android/memleak /home/android/memleak_local -maxdepth 3 \( -name '*.tmp' -o -name '*.part' -o -name '*.new' \)
   ```
   预期：两条都为空。非空说明停止打断了 dump，执行者必须把该文件名与字节列进收口账，不得静默删除。
5. 本地暂存副本可以回收，但前置条件是逐份与中心存储做字节比对（§6 第 3 步）且全部相等；比对未通过的条目保留原样。删除属不可逆动作，执行者必须先取得 Owner 授权。

## 6. SOP C：只读巡检（不改任何状态）

适用条件：值班者要回答「现在监测健康吗、覆盖多少设备、产物在不在」。

1. 取 fleet 状态（每台）：版本 sha、进程三元组、`deadline_epoch` 与剩余分钟、监测日志 mtime 龄、当日 `本轮检查完成` 计数、`[ERROR]`/`[WARNING]` 计数、`adb devices` 在线数与 535 族数。
   预期：进程三元组 = `python3` 1 + `timeout` 包装 1 + `watchdog.sh` 1；日志龄 ≤5 分钟；轮次计数按 300s 递增。
2. 取覆盖与产物（**从任一台主机的中心存储挂载点统计一次**，不得在 20 台上分别累加——那是同一份数据的 20 倍）：
   战役目录数、设备子目录数、`leak_*.hprof` 份数与字节、`meminfo_heap*.txt` 份数。
   预期：设备子目录数 = 535 族在线数；曲线份数 = 设备子目录数 × 3。
3. 取镜像一致性（每台对自己暂存的每一份做字节比对）：
   ```bash
   for f in $(find /home/android/memleak_local -name 'leak_*.hprof' -newermt "<窗口起点>"); do
     b=$(basename $f); r=$(find <中心存储挂载点> -maxdepth 3 -name "$b" -print -quit)
     [ -n "$r" ] && [ "$(stat -c %s "$f")" = "$(stat -c %s "$r")" ] && echo OK $b || echo BAD $b
   done
   ```
   预期：无 `BAD`。出现 `MISSING` 时执行者只报告，不得代删或代拷。
4. 水位：`df -Pm <中心存储挂载点>`。监测工具自身的门槛是「中心存储剩余 < 20GB 时不再写远端、只留本地」（模块 readme）。清理手段见 [`center-storage-hardlink-dedup.md`](./center-storage-hardlink-dedup.md)，执行者不得为腾空间直接删战役目录。

## 7. 取数与远程执行的坑（以下各条为要求级清单，条目内不再重复强度词）

| # | 现象 | 后果 | 正确做法 |
|---|---|---|---|
| 1 | 在 `ssh <host> "bash -s" < 脚本` 里调用 `adb shell`，而不给 adb 重定向 stdin | `adb shell` 把**脚本剩余部分当输入吃掉**，表现为「第一行之后凭空消失」，极易被误读成设备或主机无响应 | 每条 adb 调用尾部接 `</dev/null`；出现「只回一行」先怀疑本条，再怀疑设备 |
| 2 | 用 `ssh <host> "bash -s"` 却把命令写在 ssh 的参数里 | 远端从 stdin 读脚本，参数被忽略 → 调用挂死到超时 | 内联命令用 `ssh <host> '<cmd>' </dev/null`；脚本文件才配 `bash -s` |
| 3 | `pgrep -fc '[m]emleak_monitor'` 写在内联 ssh 命令串里 | 远端 shell 自身 cmdline 含该串 → 计数虚高（本役实测把 2 读成 3） | 真实进程判据用 `ps -C python3 -o cmd --no-headers \| grep -c '[m]emleak_monitor_2d.py'`；或把判据放进脚本文件 |
| 4 | `awk '{print length}'` 量行宽 | 数的是**字节**，中文按 3 字节虚高，误判「超 120 列」 | 行宽用 `python3` 的 `len()` 按字符算 |
| 5 | `grep -c '<串号>'` 当错误数 | 数的是所有含该串的行（含 INFO），量级差 2 个数量级 | 先 `grep '\[ERROR\]'` 再计数，并给出时刻分布 |
| 6 | 在中心存储（CIFS）上用 python 的 `glob` + `getmtime` 判新鲜度 | 结果不稳定、漏判当天文件 | 用 `find -newermt "<日期> 00:00"` 或 `stat -c %Y` 逐文件判 |
| 7 | 战役目录日期段写成 `%Y%m%d` | 找不到目录，误报「产物缺失」 | 目录名格式是 `_YYMMDD_Systeminfo`（例 `261119`） |
| 8 | 从挂载点直接 `find -maxdepth 2` 找 hprof | 少一层，恒为 0 | 路径是 `<挂载点>/<战役目录>/<串号>/leak_*.hprof`，从挂载点算是 **depth 3** |
| 9 | `ls -t logs/*.log \| head -1` 取监测日志 | 抓到 `watchdog.log`（300s 一跳，心跳更好但**不是**监测日志） | 用 `ls -t logs/MonkeyTest_*.log \| head -1`；两个日志分别报 |
| 10 | 把启动日写进日志名规律 | 进程不重启就一直写启动那天的文件名，误判「今天没日志」 | 判断存活用 mtime 龄，不用文件名日期 |
| 11 | 在循环里用 `head -N` | SIGPIPE 使后续命令整段被吃掉，结果偏少 | 用 `sed -n 1,20p` 或先落文件再截 |
| 12 | 用 `nohup … &` 挂长任务 | 会话回收时整组进程被杀，任务静默消失（本役实测：快照脚本跑完采集却没出汇总） | 用 `setsid nohup … >/dev/null 2>&1 </dev/null &`，并在下一轮**回读产物文件**确认它真落盘 |
| 13 | 直接比较设备侧时间戳与主机/Jira/平台时间 | 设备普遍无 NTP（实测慢 12–65 小时）；Jira 是 UTC；平台 API 是 CST | 全部换算到主机钟后再比；报告里标注用的哪个钟 |
| 14 | 在 20 台主机上分别统计中心存储产物再相加 | 同一份数据被算 20 倍 | 全局量从任一台的挂载点统计一次；只有「主机本地暂存」才逐台累加 |
| 15 | 把中心存储上人工归集的战役目录当作「平台已抓到」的证据 | 人工归集与平台上传是两条链，前者不证明后者 | 核对平台面走 Execution Registry / scan / uploader 与设备日志事件的 `remote_path`；只读诊断口径见 [`production-diagnostics.md`](./production-diagnostics.md) |
| 16 | `gh pr checks`、`git ls-remote` 不加超时 | 偶发长时间阻塞，占住整个批次 | 一律 `timeout 60 …` 包裹 |

## 8. 收口账模板与本次实证

模板（执行者收口时逐格填数值，不留空即视为未验）：监测区间与空窗、fleet 版本一致性、覆盖（在线/白名单族/战役目录/设备目录/曲线份数）、产物（份数与 GB，按日）、一致性（暂存=中心存储字节相等的比例）、异常（ERROR 时刻与串号）、水位（中心存储使用率与剩余）、交付物索引（PR / 归档文件 / 快照路径）。

本次窗口实证（2026-11-16 17:48 起，2026-11-19 14:13 由 Owner 指示提前收口）：

| 项 | 结果 |
|---|---|
| 版本一致性 | 停前 20/20 = v11（`8a6c1541`）；下发前 20/20 = v10（`307f43b6`）持续两天，印证 §3 的反例 |
| 空窗 | 3 台零覆盖主机（无白名单族设备）提前做金丝雀，无空窗；其余 17 台在 11-18 18:04–18:35 串行换版本，单台空窗 4.2–35.3 分钟 |
| 覆盖 | 白名单族 210 台，战役目录设备子目录合计 210，曲线 630 = 210×3（两份口径互校一致） |
| 产物 | 中心存储 4 日合计 130 份 hprof ≈ 30.6GB（11-16 20 份 3.1GB、11-17 35 份 5.8GB、11-18 47 份 13.1GB、11-19 28 份 8.6GB） |
| 一致性 | 抽样逐份字节比对全部相等，`MISSING` 0；停止后无 <1MB 的可疑截断文件、无 `.tmp/.part/.new` 残留 |
| 主机本地暂存 | 241 份 ≈ 60.2GB（单台最大 23.1GB），属已镜像副本，回收前必须按 §5 第 4–5 步先比对再取授权 |
| 水位 | 中心存储 90% used / 1413GB 可用；日增约 9–10GB 时的量级由**单个 (设备,进程) 键的重复抓取**主导（11-19 当日 28 份全部来自同一个键） |
| 判据结论 | system_server 连续四日零触发、当日最高堆占比 49.5%；抓取压力集中在 launcher3/settings 侧 |

## 9. 设备与主机侧已知形态（结论，非判据）

- **两台主机 USB 总线上没有任何设备枚举**（`lsusb` 只有 root hub、sysfs 设备数 0、adb server 正常监听 5037、监测进程健康）。按 [`host-device-visibility-triage.md`](./host-device-visibility-triage.md) 的分层，这属最底层（供电/上行/BIOS），执行者与 Agent 都不得用 `adb kill-server`、重置 xHCI 之类的软件动作去「试」；需现场处理。换脚本版本不改变该现象（已在 v11 上复测）。
- **个别设备存在慢性 adb 抖动**：同一台设备在多日里出现零散的 `getprop` / `dumpsys meminfo` 失败，每轮即恢复，堆曲线继续写入。结论按「瞬时传输抖动」记录，不得升级成「设备掉出监测」，除非曲线文件 mtime 龄超过若干轮。
- **非白名单族设备会长期挂在部分主机上**（实测某日 258 台在线中 48 台非白名单族，集中于 5 台主机）。监测工具自 v10 起过滤、v11 起按串号去重日志。执行者建议按实测拟合评估代价（本役拟合：每轮秒 ≈ `1.11×白名单台数 + 0.21×非白名单台数`）后再决定是否再加排除清单，不得凭印象认为「过滤掉就省很多」。
- **设备上的 monkey 不由本工具负责**：监测工具的 monkey 面默认停用（`MEMLEAK_MONKEY`），Monkey 的启停与残留清理属平台专项。执行者不得代平台拉 monkey，也不得代删平台遗留的 monkey 进程；发现异常只报告。

## 10. 未决与建议

- 建议 Owner 为「批量换版本」固定一个窗口边界时刻（例如收口日 18:00 后一次性串行下发），避免把换版本空窗摊到观测中间。
- 建议在下发前把「预计空窗分钟数 × 台数」写进授权请求，让 Owner 用容量而非感觉判断。
- 主机本地暂存的 60GB 级副本目前没有回收流程；若形成惯例，建议由工具侧补一个「比对通过才删」的脚本，而不是每次人工 `rm`。
- 本文的坑清单只覆盖 2026-11 这一役；执行者每次新踩到坑时，建议追加到 §7 并附实测数字。
