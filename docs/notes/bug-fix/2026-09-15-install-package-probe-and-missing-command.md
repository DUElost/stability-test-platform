# 238 现场：装出来的站点缺 exportfs——包判据用错 + 缺命令崩溃（#2181 / #2197）

Status: implemented
Class: bug-fix

## Decision

城市 B 站点（238）首次跑「自建导出 + 监控栈」的安装：

```text
install: running S0–S4 for site 'city-b' on 'android'
  File "/srv/stp-bundle/tools/site_config/stages.py", line 679, in stage_s2_release_env
    if ctx.ops.run(["exportfs", "-ra"]).returncode != 0:
FileNotFoundError: [Errno 2] No such file or directory: 'exportfs'
```

两个独立缺陷叠在一起：

1. **包判据用错**：S1 用 `dpkg -l <pkg>` 的返回码判断「装没装」。`dpkg -l` 对
   **已知但未安装**的包同样返回 0（状态列是 `un`），于是 `nfs-kernel-server` 被判为已装、
   apt 被跳过，导出命令根本不存在。判据必须是「可执行文件在不在」——那才是后面要用的东西。
2. **缺命令把安装崩掉**：`LocalOps.run` 直接 `subprocess.run(...)`，命令不存在时抛
   `FileNotFoundError`。调用方本来准备好把「外部命令失败」映射成稳定检查码（`install_export`），
   却永远走不到那一步；操作者看到的是 traceback 而不是可修的检查项。

## 修法

- **判据换成 `command_exists`**（PATH 里可执行）：
  - 导出：`exportfs` 不在 → `apt-get install -y nfs-kernel-server`；**装完仍不在 → FAIL
    `install_export`**（绝不带病进 S2）；
  - 监控：`prometheus` / `prometheus-node-exporter` 不在 → 装两个包；装完仍不在 → FAIL
    `install_monitoring`。
  - 顺带删掉对 `dpkg` 的依赖：判据只剩一个原语，也覆盖「包管理器说装了、命令却没有」这类
    错配（手工删过二进制、路径被策略拦）。
- **`LocalOps.run` 不再抛异常**：`FileNotFoundError → rc 127`、`PermissionError → rc 126`
  （与 shell 语义一致），调用方的 returncode 判据照常生效；失败不再以 traceback 形式出现。
- **导出块的 dry-run 早返回改 if/else**：S1 声明段不再互相短路（同一次改动里顺手收口，
  否则后加的声明段在 `--dry-run` 下会被前一段的 `return` 跳过）。

## Decision（续）：/etc/default/* 不能套本站标记守卫

修掉上面两条后现场复跑到 S4 又 FAIL `install.s4.shared_paths`：`/etc/default/prometheus` 与
`/etc/default/prometheus-node-exporter` 是**包自带的资产**（内容 `ARGS=""`），出厂就不带本站渲染
标记，而共享路径守卫的判据正是「文件里有没有本站部署根」——用它当判据，监控栈在任何一台
装了发行版包的机器上都装不上。

分类修法：

- **本站资产**（prometheus.yml、采样器脚本与单元、backend 单元、nginx、logrotate）继续走
  共享路径守卫（带 `<deploy-root>` 标记即本站）；
- **发行版默认值**改用「渲染标记属于谁」：带**别站**标记 → fail-closed（同机第二站点会静默
  改掉第一站点的监听端口与配置）；裸发行版默认值或运维手改 → 先备份到 `state/shared-path-prev/`
  再覆盖（`install_shared_asset` 既有行为），重跑幂等。

## Decision（续二）：enable --now 不重启已 active 的单元

第三次复跑：S4 的 `install.s4.monitoring` FAIL。三个单元都是 active、配置文件也都写好了，
但 Prometheus 日志里加载的是 **`/etc/prometheus/prometheus.yml`**（发行版自带配置）——
apt 装包时发行版就把 `prometheus.service` 按默认参数拉起来了（:9090），`systemctl enable --now`
对已经 active 的单元不会重启，我们写进 `/etc/default/prometheus` 的 `$ARGS` 从未生效，
`/-/ready` 自然打不到 9091。

修法：`enable` + `restart` 两个动作（与 backend 单元「restart 而非 enable --now」同一教训：
EnvironmentFile/启动参数只在启动时读取）。回归用例改为同时断言 `systemctl enable <unit>` 与
`systemctl restart <unit>`。

## Alternatives

- **改成 `dpkg -l <pkg> | grep -q '^ii'`**：能判对，但仍是「问包管理器」而不是「问能力」；
  手工装过/走别的渠道装的部署会被误判为没装而重装。用 `command_exists` 直接对齐后续调用。
- **只在 S2 外面包一层 try/except**：能止住崩溃，但缺命令仍会被当成「安装失败」的模糊原因，
  且每个调用点都要包一遍；在 `Ops.run` 里把缺命令变成结果（127）才是根因修法。
- **把 `nfs-kernel-server` 加进 BASE_DEPENDENCIES**：那是 S1 一开始就 fail-closed 的基础依赖
  清单（python3/nginx/systemctl），装包属于声明段的职责；混进去会让「先声明后安装」的
  阶段语义变糊，也会让不需要导出的站点被迫装 NFS 服务端。

## Verification

- 新增/调整用例（`tests/test_site_install.py`，**48 passed**）：
  - 「apt 报成功但 `exportfs` 仍缺」→ FAIL 且**不再出现 `install.s2.export`**（缺命令在 S1 就拦住）；
  - 「`exportfs` 已在」→ 不调 apt（回归守卫：现场就是被跳过的安装害的）；
  - 「监控包装完仍缺二进制」→ FAIL `install_monitoring`；
  - `LocalOps().run(["不存在命令"])` → **rc 127 + "command not found"**，不抛异常；
  - 正常路径用例改用 `InstallingFakeOps`（apt 之后命令可用）以匹配新判据。
- `tests/` **1046 passed**；`ruff check tools/` 通过。
- **现场复跑（238）**：本修复随下一次安装生效，结果见 PR 与后续评论。

## Revisit

- **`install.sh --dry-run` 在「升级已装站点」时会报 `install.s3.schema` FAIL**：dry-run 不落
  新树，而 S3 的 head 比较读的是**部署根**里的 alembic（仍是旧树），于是必然不匹配。这是
  dry-run 的已知局限（不是缺陷判据本身错），操作者按「dry-run 看计划、正式跑看结果」理解；
  若要修，应让 dry-run 从发布物读 head 而不是从部署根读（后续切片）。
- **`command_exists` 依赖 PATH**：以 root/受控 sudo（secure_path 含 sbin）运行没问题；
  若将来支持非 root 安装，需要改成绝对路径探测。
- 监控栈的 `apt-get install prometheus prometheus-node-exporter` 会拉进 smartmontools 等依赖
  （实测下载+配置约十分钟），默认装的站点要有这个心理预期；离线站点需要自带介质。
- **一台机器只能有一个站点装监控栈**：`/etc/default/*` 与 9091/9100 端口都是主机级单例，
  第二站点会被上面的别站标记判据挡住。若将来真要多站点共用一台机器，应改为每站点独立端口 +
  独立 unit（届时把 `/etc/default` 换成站点自己的单元文件）。
