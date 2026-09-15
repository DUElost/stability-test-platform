# 多站点 P1：#2197 站点本地监控栈（Prometheus + node-exporter）

Status: implemented
Class: feature

## Decision

让每个站点自带 `/storage` 页的数据源：站点安装器装 **Prometheus + node-exporter**，
默认装、只听回环，抓取配置由安装器渲染。此前仓库只有告警规则
（`deploy/prometheus/alerts-*.yml`）与宿主进程内存采样器，**没有 prometheus.yml、也没有
任何 unit/ARGS 模板**——城市 A 靠机房另一台 Prometheus 现刮本机，独立站点没有这台机器，
于是页面永远空着。

- **站点输入**：`monitoring.enabled`（默认 `false`；`init` 生成时写 `true`）+
  `monitoring.prometheus_port`（默认 `9091`，范围 1024–65535）。旧站点输入（无该段）仍合法。
- **端口与 job 名就是后端默认值**：`STP_PROMETHEUS_URL` 未设时后端查
  `http://127.0.0.1:9091`，`STP_CONTROL_PLANE_NODE_JOB` 未设时用 `file-server`。栈按这两个
  默认值落地，**后端零改环境**——这是刻意的：装监控栈的唯一目的就是让页面出数据，
  再多一个「记得改环境」的步骤就是一条新的静默失败路径。
- **S1**：`dpkg -l` 探测后装 `prometheus` + `prometheus-node-exporter`（发行版包）。
- **S2**：渲染（占位符 `_render` + kebab-case 精确判据）：
  - `/etc/stp/prometheus/prometheus.yml`（job `file-server` → `127.0.0.1:9100`）；
  - `/etc/default/prometheus`（`--web.listen-address=127.0.0.1:<port>`、config.file、
    TSDB 路径与 30d 保留）；
  - `/etc/default/prometheus-node-exporter`（回环 + `--collector.nfsd` + textfile + 伪 fs 过滤）；
  - 写这些文件**之前**核对发行版 unit 确实以 `$ARGS` 读 EnvironmentFile，读不到即 FAIL。
- **S4**：把采样器（`stp-mem-top.sh` + service + timer）与上面三类配置作为共享路径资产落地
  （走既有 `install_shared_asset` + 归属守卫），建 textfile 目录，`enable --now`
  node-exporter / Prometheus / `stp-mem-top.timer`，并实测 `/-/ready` 才报 PASS。

## Alternatives

- **自带 unit（`stp-prometheus.service` 等）**：与发行版 unit 并存时两个进程会写同一份
  TSDB（损坏风险），要额外 disable 发行版服务；改用「发行版 unit + `/etc/default` ARGS」
  这条标准路径，并加「unit 必须读 `$ARGS`」的 fail-closed 守卫。
- **写死 `--collector.textfile.directory=/var/lib/node_exporter/textfile`**：与仓库既有采样器
  的落点（`/var/lib/prometheus/node-exporter`，且 `stp-mem-top.service` 的 `ReadWritePaths`
  只放这一个）不一致，会装出一个永远读不到采样的栈；改为跟随既有约定。
- **只 `systemctl enable --now` 就算通过**：unit 起得来但 `/-/ready` 可能永远不满足
  （端口/配置被忽略），页面仍空；改为实测 `/-/ready`（与 `await_health`/`await_frontend`
  同一模式，测试可打桩）。
- **同时装 Alertmanager 并挂告警规则**：需要站点提供 `ALERT_WEBHOOK_URL`，且后端 `/metrics`
  默认要求 Bearer 或 X-Agent-Secret——秘密进 Prometheus 配置需要单独裁决，故不在本切片。
- **Agent 机队也装 node-exporter**：`/storage` 的 Agent 侧信息来自 Host.mount_status（DB），
  不需要 Prometheus 跨主机抓取；装机队 node-exporter 会多开 9100 端口面。留待有需求时再裁决。
- **让运维手工装监控栈**：与「一站式安装」相冲，且这正是 238 现场 `/storage` 空着的成因。

## Verification

- 仓库离线（worktree，base=origin/main `44c8eb53`）：
  - `tests/test_site_install.py` **37 passed**（新增 7 条：装包+落地+三单元启用+`/-/ready`、
    抓取配置与后端默认值对齐、自定义端口渲染、**发行版 unit 不读 `$ARGS` 即 FAIL 且不写
    `/etc/default`**、`/-/ready` 不就绪即 FAIL、`enabled: false` 一个字节都不动、dry-run 只报计划）；
  - `tests/test_site_config.py` + `tests/test_site_bootstrap.py` **225 passed**（新增 4 条：旧输入
    缺省关闭、显式端口、特权/越界端口拒绝、`init` 默认写 `enabled: true`）；
  - `tests/ -k "site or verify"` **421 passed**；`ruff check tools/` 通过。
- **实现过程中被测试抓出的缺陷**：监控产物的渲染守卫最初复用了 unit/nginx 的「有尖括号即
  未替换」判据——采样器是 shell 脚本（`<`/`>` 是重定向），一装就 `install_conflict` FAIL；
  改为 kebab-case 精确判据（`<prometheus-port>` 这类），并按发布物形态把
  `deploy/prometheus/` 加进测试 bundle。
- **pending（现场）**：城市 B 站点重跑 `install` 后核对 `/storage` 页出数据（容量、趋势、
  NFS 服务端指标、宿主进程内存面板）。

## Revisit

- **告警链路仍未闭环**：规则文件没有挂进 Prometheus（`rule_files`），Alertmanager 也没装——
  现场若要告警，需先裁决 webhook 与 `/metrics` 的鉴权方式（Bearer token 或 X-Agent-Secret
  进配置）。
- **端口冲突**：`prometheus_port` 可配，但 9091 若被别的服务占用，安装器不会自动改口——
  `/-/ready` 未就绪会 FAIL，由操作者改端口重跑。
- **Debian/Ubuntu 打包耦合**：`/etc/default/<unit>` + `$ARGS` 是 Debian 系约定，已用守卫
  兜底（读不到即 FAIL）。换发行版需要自带 unit 并解决 TSDB 独占问题。
- **无 exporter 版本下限校验**：`--collector.nfsd` / `--collector.filesystem.mount-points-exclude`
  需要 node-exporter ≥ 1.0（Ubuntu 22.04 起满足）。若站点源里的版本过旧，表现为存储页
  NFS 指标缺失，`up` 仍为 1——需要时再加版本断言。
