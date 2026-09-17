# 告警规则从没能上线：仓库 17 条 vs 生产 10 条的根因是安装清单缺项（#2488）

Status: implemented
Class: process

## Decision

1. **补两层缺口，而不是手抄一次。** 给 `tools/site_config/stages.py` 的 `MONITORING_RULES`
   加上 `deploy/prometheus/alerts-stability-platform.yml → etc/stp/prometheus/rules/…`，并给模板
   `deploy/prometheus/prometheus.yml` 补 `rule_files: [rules/*.yml]`。只修其中一层都不成立：
   没有清单，规则不落地；没有 `rule_files`，落地了也不加载。
2. **守卫单元同批纳入清单**（`stp-script-guard.{service,timer}`）+ `enable --now` 守卫 timer。
   否则新站点会有指标、有规则、却没有产生指标的那个执行者——正是本单被 24h 审计指过的同一失效模式。
3. **规则文件加 `Rendered by the site installer for <deploy-root>` 头。** 它进的是"本站资产"集合
   （`monitoring_site_assets()`），走共享路径归属守卫：目标已存在但不带本站标记时
   fail-closed 报 `install_conflict`，而不是静默覆盖运维手改——这比"能装上"更重要。
4. **本机（生产控制面）单独手工同步**：这台机器的 Prometheus 用 Debian 路径
   `/etc/prometheus/prometheus.yml`（其 `rule_files: rules/*.yml` 早已存在），并非 installer 的
   `/etc/stp/prometheus/`。它是 installer（#2197/#2283）之前的存量部署，本次只做"覆盖规则文件 +
   `POST /-/reload`"，并把原文件备份；**不**把它迁移到 installer 路径（那是另一件事，见 Revisit）。
5. **孤儿规则文件只登记不动**：`/etc/prometheus/rules/host-memory-draft.yml`（3 条）在仓库里
   没有对应源文件。删它可能砍掉某人在用的告警，纳管它又不知道是谁的意图——先记在这里。

## Alternatives

- **只在本机手工装一次**：否决。这正是漂移的成因（7/10 拷过一次，之后 7 条新增无人重放），
  再手抄一次只是把同一个坑留给下一个人。
- **新写一个 `tools/dev/sync_prometheus_rules.py`**：否决。`site_config` 已有渲染、备份、归属
  守卫、`install_conflict`、`--dry-run` 全套机制，再造一套就是"同一事实两套标准"（本仓反复
  强调的那种债）。
- **让守卫/规则装完自动 `systemctl restart prometheus`**：否决。`--web.enable-lifecycle` 开着，
  规则变更用 `POST /-/reload` 即可；重启会打断抓取，而规则加载失败在 `/api/v1/rules` 里可见。
- **把 `for: 7d`／`48h` 阈值先调保守再上线**：否决。7 条缺口规则逐条实查当前求值均为
  `now-series=0`（不会上线即 firing），无需为降噪改语义；改了反而与场景文件脱节。

## Verification

- `tests/test_site_install.py` → **66 passed**，其中新增两例：规则与守卫单元确实落地（含
  渲染标记、占位符已替换、`rule_files` 存在、`systemctl enable --now stp-script-guard.timer`
  被调用）、以及源文件仍是合法 YAML 规则集（≥19 条）。
- `promtool check config deploy/prometheus/prometheus.yml` SUCCESS；`check rules` **19 rules
  SUCCESS**；`test rules` SUCCESS，本机 2.53.3 与 CI pin 的 **3.13.3 双版本**都跑过。
- 上线前逐条求值缺口规则（`/api/v1/query?expr=…`）：7 条全部 `now-series=0`。
- `scripts/run_gates.py check:quick` → 10 gates rc=0；`python:3.11-slim` compileall rc=0。
- 本机 reload 后：`/api/v1/rules` 加载条数由 13 → 目标值，`/api/v1/alerts` 的 firing 清单
  （执行结果记在 PR #2488 与本 note 的落地段）。

## Revisit

- ~~**漂移检测本身还没有执行者**~~ **已接**（后续 PR）：`tools/dev/check-monitoring-assets.py`
  逐资产比对，执行者复用 `check-deploy-source.sh`（每次部署前 + backend unit 的 `ExecStartPre=-`），
  只 WARN 不阻塞部署。**没有按本条原设想走 `verify.py`**：S6 验收要 admin 凭据、要绑一台设备、
  要跑 noop 链，当常态检查太重；而且它只在验收那一刻跑，恰恰错过「改了仓库没重跑安装」这个
  真实窗口——部署守卫才是与漂移同时发生的那个点。
- 本机与 installer 的路径分叉（`/etc/prometheus` vs `/etc/stp/prometheus`）终归要收敛，
  否则这台最重要的机器永远在机制外；收敛方案属站点接管话题（#2283 的接管判据已具备）。
- `host-memory-draft.yml` 需要它的作者确认：纳管进仓库，还是删除。
- 7d／48h 两个阈值是拍的，按真实噪声跑两周后复议。

**补记（同日，漂移检测落地时抓到的本机实况与两处自伤）**：

- 检测器第一次跑本机报 7 项「漂移」，其中 **6 项是假漂移**：`--deploy-root` 默认取了脚本所在
  仓库根，而在 `.wt/<slug>` 里跑时那是 worktree，生产单元渲染的却是主检出路径。改成**探测运行中的
  `stability-backend` 的 `WorkingDirectory=`**（「生产到底在哪棵树上」的事实），探测不到才回退，
  并把依据打印出来。教训：任何把「仓库根」当生产事实的工具，都得先问一句是谁在说这话。
- 第二处自伤在测试里：E2E 用 `REPO_ROOT/venv/bin/python` 起子进程，而 **worktree 里没有 `venv/`**
  ⇒ 子进程 `FileNotFoundError`、检测根本没跑，断言却照样通过。改用 `sys.executable`，并加反向自证
  （stderr 出现 `No such file` / `usage:` 即红）。这是本会话**第三次**撞「被测路径被替掉/空转」同一
  形态（前两次：#2430 的 importlib 绕过路径形态、#2457 的 FakeClient 替掉真实登录序列）。
- 真实漂移 4 项，就是检测器的价值所在：
  - `/etc/default/prometheus-node-exporter` 装的是 **Debian 出厂默认**，本站的 `--collector.nfsd`
    与 `--collector.textfile.directory` **从未生效**——#2197 为存储页要的那批 NFS 服务端指标是空的；
  - `/usr/local/sbin/stp-mem-top` 是**旧版脚本**，缺 #2016 的控制字符规范化（一个异常 `comm` 含换行
    就能让整份 `stp_hostproc.prom` 解析失败、全部 `stp_hostproc_*` 序列消失）；
  - `stp-mem-top.{service,timer}` 只差 installer 的归属标记头（功能等价）。
  没有擅自同步生产：改 node-exporter 的 ARGS 要 restart（打断抓取）、换采样器会动生产指标形状，
  都属需运维批准的变更；检测器的职责是让它可见、可复查，并在下次部署前自动提醒。

**补记二（同日 14:2x，经批准把真漂移同步进生产）**：

- 落地 4 项：`/etc/default/prometheus-node-exporter`、`/usr/local/sbin/stp-mem-top`、
  `stp-mem-top.{service,timer}`；备份统一后缀 `.bak-stpguard-202609171422`。检测器复跑
  **7 match / 0 drift / 2 skipped，rc=0**。
- 效果可量化（不只是"文件对齐了"）：`node_nfsd_connections_total=48`、
  `node_nfsd_disk_bytes_read_total≈175MB`、`…_written_total≈860MB` 已进 Prometheus ⇒
  #2197 的存储页 NFS 服务端数据源第一次真的有数。附带收掉一个暴露面：node_exporter 原本
  监听 `*:9100`，按仓库 ARGS 收窄成 `127.0.0.1:9100`（查证过当时只有本机 Prometheus 在抓）。
- 覆盖前逐条核过那条运维手改（排除 `home/android/sonic_agent`）：该挂载点当前不存在，且
  仓库正则的 `var/lib/.+` 覆盖更广 ⇒ 用仓库版**无损**。若以后又挂 Android 设备，需要的是
  仓库模板支持站点差异，而不是回到手改 `/etc`。
- 一次**白重启**：脚本先按主检出路径找检测器（它还在 #2517 分支上）⇒ 安装计划为空却仍
  执行了 restart，node_exporter 无配置变更被重启一次（约 15s 抓取空窗）。记在这里是因为
  "先验证计划非空，再执行不可逆动作"本该是流程的一部分。
- 又抓到一次**假漂移**：主检出工作树当时被别的 Execution 切在 `refactor/1520-*` 上，
  `--repo-root` 指过去就把已装规则比对到了那棵树里的旧源文件。⇒ 检测器现在自己打印
  `事实源 = <path> @ <branch> <sha>`，非 main 时附「可能假漂移」提示（不改退出码）。
  也反过来印证了接线的选择：`check-deploy-source.sh` 先校验「树在 main」，走它的人不会吃到这个坑。
- **`/etc/default/prometheus` 故意没同步**，保持 `skipped`：现装的启动参数是硬写进
  `stability-backend`/prometheus unit 的 `ExecStart`（`--config.file=/etc/prometheus/prometheus.yml`、
  `--storage.tsdb.path=/var/lib/prometheus/metrics2/`），而仓库版会改成 `/etc/stp/prometheus/…`
  与不带 `metrics2/` 的路径——那是能让历史数据在面板上"消失"的变更，属路径收敛议题（#2283
  接管话题），不该混在一次"补 nfsd 采集器"的同步里。
