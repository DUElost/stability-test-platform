# 运维与部署文档索引

---

## 1. 上线前必读

| 文档 | 用途 |
|------|------|
| [`production-minimum-deployment-checklist.md`](../production-minimum-deployment-checklist.md) | 生产最小部署、env、HTTPS、冒烟 |
| [`2026-08-29-post-review-deploy-runbook.md`](./2026-08-29-post-review-deploy-runbook.md) | 审查收口后一次升级（migration + Agent 重启 + 冒烟；§7 = v2.5/P2/G15 增量） |
| [`preprod-drill-runbook.md`](../preprod-drill-runbook.md) | 预发布逐条验收 |
| [`acceptance/00-platform-smoke.md`](../acceptance/00-platform-smoke.md) | 验收 ID 与测试映射 |
| [`new-specialty-onboarding-runbook.md`](./new-specialty-onboarding-runbook.md) | 新建专项 / 适配新项目：项目登记 → 脚本入库 → 建 Plan → 试运行 → 上线检查单（G21） |
| [`production-diagnostics.md`](./production-diagnostics.md) | 生产控制面只读诊断、凭据来源与安全边界 |
| [`installation.md`](./installation.md) | **独立站点安装**：`deploy/preflight.sh` → `install.sh` → `agent/install.sh` → verify → handover 全流程、inventory 契约与常见 Fix 对照 |
| [`site-handover-and-navigation.md`](./site-handover-and-navigation.md) | 独立站点导航入口 /site/、交接证据 handover.json（MS-01…MS-13 的 P1 映射）与签字清单 |
| [`device-lease-emergency-release.md`](./device-lease-emergency-release.md) | ACTIVE 设备租约紧急释放与回查 |

---

## 2. Agent 部署

| 文档 | 用途 |
|------|------|
| [`backend/agent/DEPLOY.md`](../../backend/agent/DEPLOY.md) | 安装、目录、systemd、热更新 |
| [`agent-version-and-hot-update.md`](./agent-version-and-hot-update.md) | 协议版本门禁、code revision、滚动升级顺序 |
| [`device-log-event-recovery.md`](./device-log-event-recovery.md) | 无 DLE 行的存量事件目录补录与重提取 |
| [`host-device-visibility-triage.md`](./host-device-visibility-triage.md) | 设备从 adb 消失 / 页面上「在线 vs USB」差值的四层判别（内核 · adb server · adbd · USB 功能集），含只读命令与 2026-09-14 实证 |
| [`honor-flash-runbook.md`](./honor-flash-runbook.md) | Honor 刷机：固件上架（manifest/latest.json）、MLD 刷机 Plan、单台验证与放量 |
| [`linux-agent-ansible-runbook.md`](../linux-agent-ansible-runbook.md) | Ansible 批量 |
| [`wsl-linux-agent-setup.md`](../wsl-linux-agent-setup.md) | WSL 联调 |
| [`tools/ansible/README.md`](../../tools/ansible/README.md) | Playbook 说明 |

---

## 3. 控制平面部署模板

```
deploy/
├── control-plane/
│   ├── env/.env.backend.example
│   ├── nginx/stability-platform.conf
│   ├── nginx/stability-platform-https.conf
│   └── systemd/stability-backend.service
├── postgres/docker-compose.yml
├── nginx/frontend-docker.conf
└── prometheus/
    ├── site-alerts.yml               # 站点安装的子集（#2643 方向 1）
    ├── alerts-stability-platform.yml # 平台全量（控制面人工副本已挂载于 /etc/prometheus/rules/；ADR-0011 正式挂载待落地，副本需人工同步——见本文件 §告警规则）
    └── alertmanager.yml
```

生产 / 预发布控制平面使用 **Linux 宿主机 systemd + Nginx** 部署；根目录 `docker-compose.yml`、`Dockerfile.*` 仅用于 **开发隔离 / CI / 容器化构建**，不是生产控制平面主入口。

---

## 4. 网络与连通

| 文档 | 用途 |
|------|------|
| [`wsl-linux-agent-setup.md`](../wsl-linux-agent-setup.md) | WSL Agent、ADB 与连通配置 |
| [`archive/host-connectivity-verification.md`](../archive/host-connectivity-verification.md) | 2026-01 SSH、挂载验证历史记录 |

---

## 5. 备份与脚本

| 路径 | 用途 |
|------|------|
| `scripts/pg_backup.sh` | PostgreSQL 备份 |
| `scripts/pg_restore_test.sh` | 恢复演练 |

---

## 6. 可观测性

- 指标：`GET /metrics`（生产建议保持 `STP_METRICS_AUTH_REQUIRED=1`，必要时叠加 Nginx IP 白名单）  
- Grafana：`docs/grafana/stability-platform-dashboard.json`  
- 告警规则：平台全量 `deploy/prometheus/alerts-stability-platform.yml`——控制面宿主
  当前是**人工副本**（`/etc/prometheus/rules/`，随仓库更新需人工重放 + `POST /-/reload`；
  落后几条**不写在这里**——那是要抄的派生量，抄一次就开始说谎：用两份文件的 `alert` 名做
  集合差即可当场得出，`tools/dev/check-monitoring-assets.py` 也按**这份平台源**逐字节比对
  该副本（#2985：同名副本与站点安装的子集文件不是同一份东西，源随落点定；副本**原样拷贝**
  即算一致，改了仓库没重放才报 DRIFT，提示行会给出去向）），
  ADR-0011 的正式挂载仍未落地；
  **站点安装的是其子集** `deploy/prometheus/site-alerts.yml`（#2643 方向 1：站点 Prometheus
  只抓本机 node-exporter，平台那批控制面指标在站点结构性无样本——装了也恒不触发，
  却让「监控就绪」看起来更完整）。两者的定义一致性由
  `tests/test_site_alert_scrape_surface.py` 的对拍守住。
  规则选择器与 `backend/core/metrics.py` 注册表的一致性由
  `tests/test_prometheus_alerts_contract.py` 守四层：**结构层**（指标/标签一致性，
  恒跑）、**场景层**（阈值 / `for:` 时间窗 / 注解逐字匹配，场景文件同目录
  `.test.yml`；夜间全量 CI 装了 pinned promtool 并使其必备，PR 路径与本机未装时
  skip——#2151）、**覆盖棘轮**（新增告警必须带场景用例，恒跑；存量缺口已于
  #2236 清零、清单为空，判据本体保留。**条数不写死**（#2663）：抄下来的计数会随规则增长变成谎话，
  是否全覆盖由该棘轮自己判）、**逐条判别力**（每次只抬一条规则的阈值，
  要求场景层红且失败可归因到该告警——证明每条场景用例各自有牙，#2236）
- 宿主机进程内存采样：`deploy/control-plane/node-exporter/stp-mem-top.sh` +
  `deploy/control-plane/systemd/stp-mem-top.{service,timer}`——每 2 分钟写
  node_exporter textfile（`stp_hostproc_anon_bytes` 按 comm + cgroup unit 聚合
  Top-10，`stp_hostproc_anon_total_bytes` 为全机进程匿名内存合计），供控制面
  健康页 `/storage` 的「进程内存 Top 10」面板经后端代理读取
  （`backend/services/file_server_monitor.py`）。采样器落地背景见
  [`notes/feature/2026-09-14-hostproc-memory-metrics.md`](../notes/feature/2026-09-14-hostproc-memory-metrics.md)
- skill 用量探针（HOLLOW 检测，#2785）：`deploy/control-plane/systemd/stp-skill-usage.{service,timer}`——
  每周一 09:30 跑 `tools/dev/skill_usage_probe.py --home /home/<deploy-user> --deploy-root <deploy-root>`
  （强信号=Claude Skill 工具调用、弱信号=Codex SKILL.md 读取，观察窗按 SKILL.md frontmatter `type`
  分型：persistent 14 天 / event 60 天；root 跑，源目录走 CLI 显式指路）。**退出码契约（#2881 起反转，
  #2984 同步文档）：`0`=跑完**——无洞 / 有洞 / 缺转录源都算跑完，「有洞」的出口是下面那组指标与告警，
  不再靠 unit failed；**`1`=探针自身异常**（判据崩溃 / 指标写不出去）→ unit failed，由
  `systemctl --failed` / journal 承接人审。指标出口：textfile
  `/var/lib/prometheus/node-exporter/stp-skill-usage.prom`（`stp_skill_usage_{hollow,unknown,broken,last_run}`），
  消费方 `StabilitySkillUsageHollow` / `StabilitySkillUsageUntrusted` 两条规则；`--strict` 只在
  `python scripts/run_gates.py check:gov`（深挖跑它）
- **host 脚本在位矩阵（第五道闸，#2958）**：常设 sweep 每天 09:30（`SCRIPT_PRESENCE_SWEEP_CRON`，
  控制面进程内作业）对「未退役 host × 目标版本集（`plan_step.enabled` ∩ `script.is_active`）」
  跑一轮 `verify_scripts` RPC（只读 sha256 核验，不占维护窗），结果落 `host_script_presence`
  （六态：`present / missing / mismatch / unknown / n_a / maintenance`）。查询：
  `GET /api/v1/script-presence/summary`（fleet 汇总 + `stale`）与
  `GET /api/v1/script-presence/hosts/{host_id}`（单机明细）；单机按需重核
  `POST /api/v1/script-presence/refresh?host_id=…`。指标
  `stability_host_script_presence{host_id,state}` + 账本新鲜度
  `stability_script_presence_sweep_timestamp`；告警两条成对：
  `StabilityHostScriptPresenceGap`（缺口 >0，维护窗与未知态不计入）与
  `StabilityScriptPresenceSweepStale`（账本缺失或 >48h 未刷新——**只装第一条会读成绿**）。
  它是 #2931 四道账之外的第五道：前四道只看 DB/部署树，本道看主机实际文件。

---

## 7. 方案 C 运维补充

Agent 新增 env（部署时写入 `/opt/stability-test-agent/.env`）：

| 变量 | 说明 |
|------|------|
| `STP_AEE_LOCAL_ROOT` | Agent HDD AEE 根 |
| `STP_AEE_NFS_ROOT` | **中心存储（CIFS）** 挂载点主键（upload / spill / dedup） |

设计详述：[`design/2026-plan-c-storage-and-access.md`](../design/2026-plan-c-storage-and-access.md)  
角色/别称：[`design/2026-storage-roles-and-aliases.md`](../design/2026-storage-roles-and-aliases.md)

---

## 8. 数据库迁移

```bash
cd backend && python -m alembic upgrade head
```

生产发布前后必须执行；见 ADR-0008。

执行协议硬化 revision（`c8d9e0f1a2b3` 等）前先跑：

```bash
python -m backend.scripts.migration.preflight_execution_protocol
```

契约说明：[`../design/07-execution-protocol.md`](../design/07-execution-protocol.md)。

清 / 截断 `job_instance` 或整库回滚时，Agent 侧善后见：

| 文档 | 用途 |
|------|------|
| [`control-plane-db-maintenance.md`](./control-plane-db-maintenance.md) | 清库/回滚后 Agent `job_terminal_outbox` 未 ack 与死信的判读与清理（先看中心指标，必要时才上主机） |

---

## 9. 事故复盘

| 文档 | 用途 |
|------|------|
| [`incident-2026-07-28-host-9-126-hard-hang-and-bios-upgrade.md`](./incident-2026-07-28-host-9-126-hard-hang-and-bios-upgrade.md) | 198.51.100.126 硬挂根因、watchdog/EEE/BIOS/Agent restart 加固、其余 19 台 host BIOS 汇总 |
| [`incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md`](./incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md) | 192.0.2.87 xHCI 主控死亡致 USB/ADB 全空 7 天：根因、unbind/rebind 恢复、与 #160 区分 |
| [`incident-2026-07-31-script-sha-drift-dispatch-outage.md`](./incident-2026-07-31-script-sha-drift-dispatch-outage.md) | 脚本版本 sha 漂移致全平台派发中断：根因、`force_rebaseline` 逃生阀、ADR-0020 CI 门禁 |

