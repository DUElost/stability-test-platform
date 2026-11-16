---
name: monkey-aee-dedup-handoff
description: Monkey 长稳专项的 AEE 事件日志跨 PlanRun 去重汇总、向 Jira 交付共享根导出代表证据、产出或回填厂商格式 Jira 输入表单的 SOP。触发时机：用户给出一个或多个 `plan-runs/<id>` 并要求「日志去重汇总」「把去重后的日志放到 15.4」「出一份可供 Jira 提交的表单」「同步真实 Count/DeviceCount」，或专项完结后需要按 build 拆分交付证据时。本文的「去重」特指按 (串号, 事件目录名) 做跨 run 事件去重、再按问题指纹做问题归并；「表单」特指厂商格式 `Result_None_None_MonkeyAEE_SH_*.xls`。
---

# Monkey AEE 日志去重汇总与 Jira 交付 SOP

> 权威细节（阶段划分、计数口径、缺口与建设建议）见
> `docs/operations/2026-10-30-monkey-aee-jira-handoff-reference.md`；日志链术语以
> `docs/design/2026-log-chain-global-semantics.md` §4 为准，存储角色以
> `docs/design/2026-storage-roles-and-aliases.md` 为准，扫描/上送/合并协议以
> `docs/design/2026-scan-upload-merge-contract.md` 为准。本文只写触发、顺序、命令模板与红线，不重复定义协议。
> 校准来源：2026-11-05（run 1179/1193/1195/1198、1190/1196、1200）与 2026-11-12（P535AA 四 run、8 个 build）实跑。
> 参考实现：`scripts/` 收录 `per-build` 管线实跑脚本的清洗版（脱敏、标注 `CONFIG`），见「附录：固化参考脚本」。

适用条件：一个或多个 Monkey 长稳 PlanRun 需要合成一份 Jira 输入；事件日志可能在中心存储、也可能仍留在 Agent host；共享交付根可写。

> 警告：本流程会向 Jira 交付共享根写入目录与表单，写错目录会污染下游 Jira 输入且难以从目录反查来源。执行者必须先向 Owner 确认下节「前置检查」的全部四项；执行者不得自行推断目标目录名与日期、不得未备份覆盖既有表单或事件目录、不得沿用其他会话的授权。

## 0. 术语与路径（本 SOP 内的限定语）

「中心存储」在本流程可能指两处不同存储，误写根目录会改变操作结果，故按下表区分：

| 本 SOP 用语 | 含义 | 本机路径 | 用途 |
|---|---|---|---|
| 日志分享盘（中心存储日志根） | 角色 4 的日志盘 | `/mnt/stp-log`、`/mnt/stp-aee` | 只读来源：`devices/<run>/`、`dedup/<run>/` |
| Jira 交付共享根 | 厂商消费的共享盘（15.4） | `/mnt/jxtinno-sonic` | 写入目标：`<folder>/<串号>/aee_exp/<事件目录>/` |
| 厂商机挂载路径 | 厂商侧同一共享盘的挂载点 | `/home/jxtinno/sonic_tinno/...` | 表单 `Path` 字段必须用它 |
| Agent host | 跑 Agent 的 Linux 机 | 短后缀记法，如 `.83`、`.86`、`.92` | `LOCAL` 事件来源 `/mnt/hdd/aee_events/<folder>/` |
| PG | 业务库 | `device_log_event`（DLE 台账） | 事件观测的唯一权威来源 |
| 扫描工具 | `start_log_scan.py` | 见 §1 | `-m 0` 本机扫描 / `-dedup_org` 离线去重 |

UNC 写作 `\\172.21.x.x\jxtinno\sonic_tinno\<folder>\`（脱敏形态，实际地址现场确认）。执行者不得在文档、PR 正文、issue 评论或脚本注释里写内网主机 IP 全地址；用 `.NN` 短后缀或 `172.21.x.x`。

变量约定（执行者在工作目录内统一使用，不得在步骤间改义）：

| 变量 | 含义 |
|---|---|
| `$WORK` | 本批次工作目录，如 `/home/debian13/tmp/<campaign>_<yyyymmdd>` |
| `$STAGING` | `$WORK/events`，暂存的最小事件集，层级 `<serial>/aee_exp/<entry>/` |
| `$TARGET` | `/mnt/jxtinno-sonic/<folder>`，Jira 交付共享根下的本批次目录 |
| `$CONS` | 某 build 的合并输入表 `$WORK/<build>/merge_input/<build>_org.xls` |
| `CUT` | 快照截止时间，格式 `YYYY-MM-DD HH:MM:SS+08` |
| `<首轮 started_at>` | 首轮 run 的 `started_at`，本次/基线拆分的下界 |

## 1. 前置检查

按 `prod-db-readonly-diagnose` skill 以只读角色直连 PG，目标取自站点当前权威配置，凭据经环境变量传入；执行者不得把连接串或口令写入命令行、脚本、文档或 PR diff。

- [ ] **确认 run 集合与终态**：查 `plan_run` 与 `job_instance`，记录每个 run 的状态、起止时间、作业数与中止原因。
      查法（`<runs>` 是逗号分隔的 run id）：
      ```sql
      SELECT pr.id, pr.status, p.name AS plan_name, pr.started_at, pr.ended_at,
             pr.total_job_count, pr.completed_job_count, pr.failed_job_count,
             pr.aborted_job_count, pr.build_version, pr.plan_id
      FROM plan_run pr JOIN plan p ON p.id=pr.plan_id
      WHERE pr.id IN (<runs>) ORDER BY pr.id;
      ```
      预期：同一 build 的 run 集合完整，包含中止尝试与补跑；`RUNNING` 的 run 单独标出。
- [ ] **确认事件观测面**：查 `device_log_event` 的 `state` 分布与跨 run 重叠。
      查法：
      ```sql
      SELECT plan_run_id, state, count(*), count(DISTINCT serial)
      FROM device_log_event WHERE plan_run_id IN (<runs>) GROUP BY 1,2 ORDER BY 1,2;

      WITH e AS (
        SELECT plan_run_id, serial,
               split_part(local_path,'/',array_length(string_to_array(local_path,'/'),1)) AS entry
        FROM device_log_event WHERE plan_run_id IN (<runs>)
      ), k AS (
        SELECT serial, entry, array_agg(DISTINCT plan_run_id ORDER BY plan_run_id) AS runs
        FROM e WHERE entry <> '' GROUP BY 1,2
      )
      SELECT runs::text, count(*) FROM k GROUP BY runs ORDER BY count(*) DESC;
      ```
      预期：能解释「早期 run 独有事件」的条数与来源；`PULL_FAILED` 行数单独记录。
- [ ] **确认 build 与产品命名**：build 取锚点 run 的 `run_context.device_builds` 逐设备映射，产品前缀取 `device.product`。
      查法：
      ```sql
      SELECT d.serial,
             (pr.run_context->'device_builds'->>ji.device_id::text) AS build
      FROM plan_run pr JOIN job_instance ji ON ji.plan_run_id=pr.id
      JOIN device d ON d.id=ji.device_id WHERE pr.id=<锚点run>;

      SELECT DISTINCT d.product, d.model, d.build_display_id
      FROM job_instance ji JOIN device d ON d.id=ji.device_id
      WHERE ji.plan_run_id IN (<runs>);
      ```
      预期：每个串号有 build；产品前缀与 `device.product` 一致（`MLD-NX3Carrier` 的判别见 `docs/notes/feature/2026-10-15-device-product.md`）。
- [ ] **向 Owner 确认四项并等回复**：① 按哪些 run 合并去重（建议含早期中止 run 的独有事件）；② 快照截止时间与 `RUNNING` run 是否按快照处理；③ 目标目录名中的日期（`<product>_<build>_<日期>_MonkeyAEEinfo`，同一 build 只建一个目录）；④ 是否并入既有目录、是否纳入基线事件、缺失事件是否补拉。
      查法：把上三步的表格与建议选项一屏列出，不自行拍板。历史里目录日期被多次改（`1102`→`1029`、`1102`→`1103`、统一 `1106`），执行者不得按首轮日期推断。
- [ ] **确认磁盘与工具**：`df -h /home/debian13` 有余量；`/home/debian13/Start-Log-Scan/venv/bin/python` 与 `/home/debian13/start-log-scan-releases/2.1.0/venv/bin/python` 均可 `import xlrd, xlwt`；`soffice` 在场。
      预期：两个 venv 的 `xlrd`/`xlwt` 均可用（实测 2.0.2 / 1.3.0）。

## 2. 标准作业流程（SOP）

按顺序执行以下步骤。每步只做一个动作；会改变状态的步骤后写「预期：」。

### 阶段 A：冻结快照并构建事件主清单

1. 取当前时刻作为快照截止 `CUT`（格式 `YYYY-MM-DD HH:MM:SS+08`），后续所有查询带 `detected_at <= '<CUT>'`。
   预期：`CUT` 写入工作目录的 `master_frozen.json` 元数据，全流程只用它。

> 警告：对 `RUNNING` 的 run，不冻结会让扫描期间新增的事件混入不同批次查询，产出不可复核的拼接表。执行者不得用多次未冻结查询的结果拼表。

2. 拉取冻结窗口内的 DLE 观测并写 `master_frozen.json`。查法：
       ```sql
       SELECT plan_run_id, serial, host_id, state, coalesce(size_bytes,0)::bigint,
              split_part(local_path,'/',array_length(string_to_array(local_path,'/'),1)) AS entry,
              coalesce(remote_path,''), local_path,
              to_char(detected_at,'YYYY-MM-DD HH24:MI:SS')
       FROM device_log_event
       WHERE plan_run_id IN (<runs>) AND detected_at <= '<CUT>'
       ORDER BY plan_run_id, detected_at;
       ```
       每行按 `(serial, entry)` 归并，记录 `runs[]`、`build`、`center`（`remote_path` 且 `os.path.isdir` 为真）、`local`、`host`、`size`。`entry` 为空的行（多为 `PULL_FAILED`）跳过并另记缺失清单。
       预期：唯一事件数等于 SQL 的 `count(DISTINCT (serial, entry))`；每个唯一事件标注来源为「中心存储已有」或「需从 Agent host 拉取」。

3. 按 build 分组统计并回报 Owner。
   预期：每 build 的设备数、唯一事件数、中心已有数、需 host 拉取数、估算大小一屏可读。

### 阶段 B：暂存事件目录

4. 对每个「中心存储已有」的事件，复制 `BUILD_INFO`、`ZZ_INTERNAL` 与 `*.dbg` 到 `$STAGING/<serial>/aee_exp/<entry>/`。
   预期：每个事件目录至少有一个 `.dbg`；`-m 0` 扫描只需这三样即可产出 `_org.xls` 与 `.DEC/__exp_main.txt`（2026-11-12 最小文件集实测）。

5. 对每个「需从 Agent host 拉取」的事件，按 host 与根目录分组，用 tar 流拉取。命令模板：
       ```bash
       ssh -o BatchMode=yes -o StrictHostKeyChecking=no android@<host-ip> \
         "cd '<root>' && tar --ignore-failed-read -cf - \
           '<serial>/aee_exp/<entry>/BUILD_INFO' \
           '<serial>/aee_exp/<entry>/ZZ_INTERNAL' \
           '<serial>/aee_exp/<entry>'/*.dbg" \
         | tar -C "$STAGING" -xf -
       ```
       `<root>` 由 `local_path` 在 `/<serial>/aee_exp/` 处切分得到；解压根必须是 `$STAGING`，因为 tar 内的路径已含 `<serial>/aee_exp/<entry>/`。
       预期：`$STAGING` 下每个事件目录都有 `.dbg`；缺失 `.dbg` 的事件单列复核。

6. 复核 host 侧事件目录存在性（逐 host 一次 SSH）：
       ```bash
       ssh ... android@<host-ip> \
         "while IFS= read -r p; do [ -d \"$p\" ] && echo OK || echo MISSING; done"
       ```
       预期：全部 `OK`；`MISSING` 的事件记入缺失清单，不静默丢弃。

### 阶段 C：扫描

7. 运行本机扫描（`-m 0` 即 AEE & TNE 模式）：
       ```bash
       cd /home/debian13/Start-Log-Scan
       ./venv/bin/python start_log_scan.py -m 0 -d "$STAGING" -p SH -end \
         > "$WORK/scan.log" 2>&1
       ```
       预期：`$STAGING/Result_None_None_MonkeyAEE_SH_<ts>_org.xls` 存在且行数等于唯一事件数；每个事件目录出现 `*.DEC/__exp_main.txt`。

> 警告：下一步的 `-dedup_org` 只有 V2.1.0 安装产出 `*_members.json`。执行者不得用 `/home/debian13/Start-Log-Scan`（V2.0.9）跑 `-dedup_org` 后期待成员映射。

### 阶段 D：合并输入表与离线去重

8. 按每个 build 生成一份合并输入表 `<build>_org.xls`，列为（顺序与表头逐字一致，`ExpType ` 带尾空格）：
       `Id, Path, Version, ExpTime, ExpClass, "ExpType ", CurProcess, Package, Detail, CausedBy, extraTag, Count, Activity, DeviceId, Rom_Ram`。
       其中 `Path` = `$TARGET/<serial>/aee_exp/<entry>/`，`Version` = build，`Count` = 1，`DeviceId` = 串号，其余字段取该事件在扫描 `_org.xls` 中的行。按 `ExpTime` 升序排序。
       预期：行数等于该 build 的唯一事件数；`Path` 全部落在交付共享根。

9. 运行离线去重：
       ```bash
       cd /home/debian13/start-log-scan-releases/2.1.0
       ./venv/bin/python start_log_scan.py -dedup_org "$CONS" -side shanghai \
         > "$WORK/dedup_org.log" 2>&1
       ```
       预期：同目录产出 `<base>_dedup_org_<ts>.xls`（问题行）与 `<base>_dedup_org_<ts>_members.json`（成员映射、过滤原因、分组统计）。

10. 读 `*_members.json`，按 `rows[].branch` 区分 `grouped` 与 `filtered`（`filter_reason` 多为 `discard_dbg_package` / `not_in_pkglist`），记录过滤条数。
    预期：`stats` 的分组数、成员数、过滤数与表单行数一致。

### 阶段 E：分组、本次/基线拆分与代表选择

11. 按 `groups[]` 构建 `groups_final.json`：每个问题记录 `count`（发生次数）、`device_count`（`device_ids` 去重数）、`device_ids`、`runs`、`first`/`last`、`rep_key`、`rep_source`、`target_exists`。
    预期：`sum(count)` 等于 `grouped` 成员数；`device_count` 等于 `device_ids` 长度。

12. 以 `<首轮 started_at>` 为界做本次/基线拆分：`ExpTime >= <首轮 started_at>` 为「本次」，更早为「基线」；`ExpTime` 为 2010 类占位值（如 `RebootException`）时回退用该事件的 `detected_at` 判定。问题类别记「本次 / 混合 / 仅基线」。
    预期：三类计数之和等于问题数；「仅基线」问题单独标注，供 Owner 决定是否提交 Jira。

13. 选代表：混合问题优先取「本次」最早事件，其余取最早事件；记录代表源路径、大小（排除 `*.DEC`）与文件数。
    预期：每个问题恰有一个代表事件目录，且源目录可读。

### 阶段 F：生成去重汇总工作簿

14. 生成 5 页工作簿：概览、问题汇总、代表日志清单、全部事件、口径说明。概览写快照时刻、run 列表、唯一事件数、问题数、本次/基线、过滤数、缺失数、目标目录与交付状态；口径说明写去重键、归并规则、Count/DeviceCount 口径、代表选择、目标目录规则、运行状态与待确认项。
    预期：问题汇总行数等于去重后问题数；代表日志清单行数等于问题数。

15. 导出 `.xls` 与 `.xlsx`（`soffice --headless --convert-to xlsx`）、三张明细 CSV、`manifest.json`，复制到稳定目录 `/home/debian13/<campaign>_<yyyymmdd>/`。
    预期：`manifest.json` 含 `snapshot_cutoff`、build 列表、逐问题事件清单与计数；目录可读。

### 阶段 G：复制代表日志到交付共享根

> 警告：本阶段写 Jira 交付共享根。执行者必须先确认 `TARGET` 目录名与 Owner 授权的「新建 / 并入」；执行者不得覆盖既有事件目录。目录已存在且内容一致时跳过，不一致时先报差异。

16. 对每个代表事件，整目录复制到 `$TARGET/<serial>/aee_exp/<entry>/`。中心存储来源用本地 tar；Agent host 来源用：
       ```bash
       ssh ... android@<host-ip> "cd '<root>' && tar -cf - '<serial>/aee_exp/<entry>'" \
         | tar -C "$TARGET" --no-same-owner --touch -xf -
       ```
       解压根必须是 `$TARGET`（不是 `$TARGET/<serial>/aee_exp`）。
       预期：目标目录含 `BUILD_INFO` 且有 `mobilelog/` 或 `*.dbg`；无 `*.DEC` 残留。

17. 把该事件扫描产出的 `*.DEC`（含 `__exp_main.txt`）复制到目标目录；已存在同名目录且已有 `__exp_main.txt` 时跳过。
    预期：每个代表目录恰有一个含 `__exp_main.txt` 的 `.DEC` 目录。

18. 逐目录核对文件数与字节数（排除 `*.DEC`），写入 `copy_manifest_*.tsv`。
    预期：全部一致；不一致的目录重传或标记失败，不得只报「复制完成」。

### 阶段 H：产出或回填 Jira 表单

19. 若 Owner 已在共享目录用厂商工具生成表单，走回填路径：
    1. 取该目录按 mtime 最新的 `Result_None_None_MonkeyAEE_SH_*.xls`。
    2. 逐行按 `Path` 解析 `(serial, entry)`，映射到问题分组；精确匹配失败时按同串号 + 事件时间前缀回退（同秒副本如 `..._110843_32412` 与 `..._110843_271` 属同一次发生，不得重复计数）。
    3. 备份原件为 `.xls.bak-<ts>` 并核对 md5。
    4. 写 `*_counted.xls`：保留全部列与行，仅把第 11 列 `Count` 与第 13 列 `DeviceCount` 改为分组值。
    5. 用临时文件 + `mv` 原子替换共享表单，回读 md5 与行数，写 `count_sync_*.tsv` 逐行旧值/新值账本。
       预期：行数不变、`sum(Count)` 等于 `grouped` 成员数、md5 与本地回填文件一致、备份 md5 与原件一致。

20. 若由本流程直接生成表单，写 `Result_None_None_MonkeyAEE_SH_<ts>.xls`：`Path` = `/home/jxtinno/sonic_tinno/<folder>/<serial>/aee_exp/<entry>/<dec>/__exp_main.txt`，`Count` = 分组 `count`，`DeviceCount` = 分组 `device_count`，`Version` = build，其余字段取问题行的代表行。
    预期：行数等于问题数；每个 `Path` 映射到本机 `/mnt/jxtinno-sonic/...` 后 `os.path.isfile` 为真。

21. 更新汇总工作簿概览的「交付状态」与「Jira 表单」两行，写明表单文件名、行数、Count 合计、备份名；重新导出 `.xlsx` 与 CSV。
    预期：汇总表与共享表单计数一致。

## 3. 后置验证

按顺序核对，任一项不满足不得报完成：

1. `master_frozen.json` 的唯一事件数、各 build 事件数、缺失清单与前置检查的 SQL 结果一致。
2. 扫描 `_org.xls` 行数等于唯一事件数；`*_members.json` 的 `grouped + filtered` 等于扫描行数。
3. 每份 Jira 表单行数等于该 build 的问题数；`sum(Count)` 等于 `grouped` 成员数；`sum(DeviceCount)` 可由 `device_ids` 复算。
4. 每个表单 `Path` 映射到本机路径后存在；UNC 与目标目录名不含内网 IP 全地址。
5. `copy_manifest_*.tsv` 全部 `ok`，目标目录无 `.DEC` 之外的半成品、无覆盖既有目录。
6. 共享表单有原件备份且 md5 匹配；`count_sync_*.tsv` 与 `manifest.json` 已归档到稳定目录。
7. 汇总工作簿 5 页可读，概览的交付状态与文件系统现状一致。

## 4. 踩坑守卫（负向约束）

- **执行者不得自行推断目标目录名与日期**。`<日期>` 由 Owner 指定；同一 build 只建一个 `<product>_<build>_<日期>_MonkeyAEEinfo` 目录。目录已存在时执行者必须按「并入」处理，只新增 `<串号>/aee_exp/<事件目录>`，不得覆盖既有事件目录或表单；更名既有目录走 `mv` 并同步更新所有目标路径与表单 `Path`。
- **执行者不得把 `plan_run.status` 当作事件集完整性的判据**。`SUCCESS`/`FAILED` 只描述 run 终态；事件来源必须是 `device_log_event`（DLE 台账）。`RUNNING` run 必须冻结快照，结束后新增事件必须补扫并重跑。
- **执行者不得只凭 `--help` 断定扫描工具参数生效**。`-m 0`（AEE_TNE）分派不消费 `-project` / `-build` / `-reporter`，报表文件名里的 `None` 来自工具本地配置 `jira_repo`/`reporter`，不是项目或版本（#3831）。`Version` 必须由合并输入表写入，不得靠 argv 传。
- **执行者不得用 `-s` / `-sz` 跑 `-m 0` 扫描**。跳过解压会产出空报表或只有 `_summary.json` 而无 `_org.xls`（2026-11-05 实跑）。
- **执行者不得混用 `start_log_scan` 的三种 argv 语义**：`-m 0` 本机扫描、`-dedup_org` 主机/离线去重、`-merge_files_list` 仅控制面 merge（术语见 `2026-log-chain-global-semantics.md` §4）。跨 run 去重用 `-dedup_org`，且必须用产出 `*_members.json` 的 V2.1.0 安装。
- **执行者不得用 `Path` 字符串直接拼接或 `cp`**。merge 报表与目标表单的 `Path` 可能是旧格式 ID 或指向别的 run；事件身份必须按 `(串号, aee_exp 事件目录名)` 解析，同一物理事件的多路径副本只保留一份。
- **执行者不得静默丢弃 `PULL_FAILED` 与缺失事件**。这类行 `local_path` 为空，必须单列缺失清单、报 Owner、不计入 `Count`；补拉只在 Owner 授权后进行。
- **宿主 tar 的解压根必须是 `$TARGET` / `$STAGING` 根**，不是 `<serial>/aee_exp`。tar 内路径已含 `<serial>/aee_exp/<entry>`；解压到内层会多套一层目录，代表日志看似复制成功但内容为空（2026-11-12 实跑踩过，必须用 `BUILD_INFO` + `mobilelog/` 或 `*.dbg` 的 `full_ok` 判据复核）。
- **复制 `.DEC` 时执行者必须用 `cp -r` 并校验 `__exp_main.txt`**，不得 `cp -a` 覆盖 CIFS 同名文件（权限报错）；每个代表目录必须有含 `__exp_main.txt` 的 `.DEC`，否则表单 `Path` 指向空文件。
- **执行者不得把表单原始 `Count` 当异常总数**。厂商生成的原始 `Count` 是代表日志的行级出现次数（恒为 1）；必须按 `(DeviceId, ExpTime, CausedBy)` 去重回填，`DeviceCount` 取不同 `DeviceId` 数、跨 run 取并集，并在交付说明写明口径。
- **执行者不得在未备份时覆盖共享表单**。覆盖前保留 `.xls.bak-<ts>` 并核对 md5；厂商工具再次重扫会重新生成 `Count=1` 的表单并覆盖回填结果，重扫后执行者必须重新回填。
- **合并输入表与 Jira 表单的第 13 列不是同一字段**：合并输入 `_org.xls` 是 `DeviceId`（串号），Jira 表单是 `DeviceCount`（整数设备数）。回填时改的是 Jira 表单；写错列会把串号写进设备数。
- **「仅基线」问题只标注建议，执行者不得自动剔除**。运行前遗留的 V56/V78 事件是否交付、是否提交 Jira 由 Owner 决定；基线拆分以首轮 run 开始时间为界，2010 类占位时间回退 `detected_at`。
- **扫描报表里不在 DLE 登记内的 `*_temp` / 预分析 / baseline 目录不得纳入**（2026-11-05 有 109 条）。纳入会把往期遗留问题重复计入本专项。
- **表单 `Path` 必须使用厂商机挂载路径** `/home/jxtinno/sonic_tinno/...`；本机校验时映射到 `/mnt/jxtinno-sonic/...`。执行者不得把本机挂载路径写进 Jira 表单。
- **执行者不得把内网主机 IP 全地址写进文档、PR 正文、issue 评论或脚本注释**；用 `.NN` 短后缀或 `172.21.x.x` 脱敏形态。
- **执行者不得把 PG 连接串、口令或 token 写入脚本、命令行、文档与 PR diff**；查库按 `prod-db-readonly-diagnose` skill 走只读角色与站点配置。
- **执行者不得把「复制完成」「命令退出码 0」当作验证通过**。必须逐目录核对文件数与字节数、回读表单行数与 md5、核对 `Path` 存在；未跑的检查标为 pending。

## 附录：关键数据形状

- **事件去重键**：`(serial, split_part(local_path,'/',array_length(string_to_array(local_path,'/'),1)))`，即串号 + `aee_exp` 下事件目录名。
- **问题归并规则**（V2.1.0 `-dedup_org`）：`ExpClass` 相同、`Package` 相同、`CausedBy` 规范化后相似度 ≥ 90 归为同一问题；工具同时按 `pkglist` 白名单过滤 `discard_dbg_package`。
- **`*_members.json`**：`stats` / `meta` / `rows[]`（`org_row`、`branch`、`filter_reason`、`group`、`final_row`）/ `groups[]`（`group`、`final_row`、`count`、`device_ids`、`member_rows`、`representative_row`）。
- **合并输入 `_org.xls` 列**：`Id, Path, Version, ExpTime, ExpClass, "ExpType ", CurProcess, Package, Detail, CausedBy, extraTag, Count, Activity, DeviceId, Rom_Ram`。
- **Jira 表单列**：同上，但第 13 列为 `DeviceCount`；`Count` 与 `DeviceCount` 为整数。
- **交付目录层级**：`<folder>/<串号>/aee_exp/<事件目录>/`，`*.DEC/__exp_main.txt` 位于事件目录内。
- **两份扫描工具**：`/home/debian13/Start-Log-Scan`（V2.0.9，用于 `-m 0` 扫描）与 `/home/debian13/start-log-scan-releases/2.1.0`（V2.1.0，用于 `-dedup_org` 与所有 `xlrd`/`xlwt` 脚本）。仓库 `venv/bin/python` 不带 `xlrd`，不得用于读写这些表。
- **实跑参考脚本**：已固化在 `scripts/`（`per-build` 6 个），清单、数据契约与红线见下节；执行者不得依赖 `/home/debian13/tmp/<campaign>_*/` 仍在场。

## 附录：固化参考脚本（scripts/）

`scripts/` 收录 2026-11 实跑 `per-build` 管线的清洗版（6 个脚本）：去掉硬编码口令与内网 IP 全地址、
标注 `CONFIG` 区与来源，供执行者按 §2 的命令模板对照复用。执行者不得把它们当成通用工具原样执行。
`batch` 形态（单批次 5 页汇总工作簿、厂商表单回填）的上游脚本未收录，执行者按 §2 命令模板自行实现。

数据链：`master_frozen.json` → `merge_input/<build>_org.xls` → `*_dedup_org_*.xls` + `*_members.json` → `result.json`。

| 脚本 | 作用 | 对应 SOP |
|---|---|---|
| `01_freeze_events.py` | 冻结 `CUT` 快照、查 PG 写 `master_frozen.json`、按 build 汇总 | 阶段 A |
| `02_stage_events.py` | 中心存储最小文件集 + Agent host tar 拉取到 `$STAGING` | 阶段 B |
| `03_consolidate_dedup.py` | 生成合并输入 `_org.xls` 并跑 `-dedup_org` | 阶段 D |
| `04_copy_representatives.py` | 整目录复制代表事件 + `.DEC`，记录失败清单 | 阶段 G |
| `05_make_jira_form.py` | 直接生成含真实 `Count`/`DeviceCount` 的 Jira 表单 | 阶段 H 步骤 20 |
| `06_verify_forms.py` | 校验各 build 表单行数、计数与 `Path` 存在 | 后置验证 |

红线：

- **执行者必须先按现场改 `CONFIG` 区**（run 集合、`CUT`、锚点 run、build / folder / 目录名、工作目录），再运行脚本；执行者不得原样执行 2026-11 的常量。
- **执行者必须用 `/home/debian13/start-log-scan-releases/2.1.0/venv/bin/python` 运行这些脚本**（含 `xlrd` / `xlwt`）；仓库 `venv/bin/python` 不带这两个库。
- **执行者必须经环境变量 `PGHOST` / `PGUSER` / `PGDATABASE` / `PGPASSWORD` 提供查库目标与凭据**；`01_freeze_events.py` 缺失任一即 fail-closed 退出，`PGUSER` 必须取只读角色。执行者不得把值写回脚本、命令行或文档。
- **执行者不得把 `scripts/` 里的参考实现当作生产工具或 CI 门禁**。脚本未参数化、未写测试、未在现场参数下复跑，只作可检索的实跑样本；权威逻辑以 §2 的命令模板为准。- **执行者不得把内网 IP 全地址写回 `scripts/`**；UNC 用 `172.21.x.x` 脱敏形态。
