# 控制面告警副本：已挂载但落后于仓库（#2880 的现场确认 + 文档同步）

Status: implemented
Class: bug-fix

- 日期：2026-09-20
- 关联：`#2880`（本单）、`#2788`（把 `StabilityPgSchemaGuessing` 移出站点子集）、
  `#2643`（站点子集口径）、`#2488`（规则进安装清单）、ADR-0011（平台文件挂载路径）、
  `#2632`（该规则服务的实质缺陷——生产库猜 schema 直查）

## Decision

issue 的判断（1）是「现场核一次控制面 Prometheus 的 `rule_files` 是否含平台文件」——**做了**，
实测结果比 issue 假设的两种情形都更具体：

| 项 | 实测（2026-09-20，只读） |
|---|---|
| 加载路径 | 控制面宿主 Prometheus（`127.0.0.1:9091`）`rule_files: rules/*.yml` ⇒ `/etc/prometheus/rules/` |
| 副本 | **存在**：`alerts-stability-platform.yml`（325 行，副本时间 2026-09-17 22:36） |
| 仓库同文件 | `origin/main` 393 行 |
| 差集 | **缺 2 条**：`StabilityHostAdbOfflineConcentration`、`StabilityPgSchemaGuessing` |
| 规则 API | 已加载 24 条、7 组；`PgSchemaGuessing` **不在其中**；`stp_pg_*` 无样本 |

⇒ 第三种情形：**挂了文件 ≠ 规则在评估**。issue 的「零评估点」担心过度（路径确实在 `rule_files`
里、规则确实被评估），但「已挂载」也不等于「与仓库一致」——**副本不会自己跟着仓库走**，
同步是人工步骤，而仓库侧没有任何判据盯着它。

### 落点（issue 判断 2/3 的综合）

改三处**活文档**（各加一句实测结论 + 同步义务，不新建机制）：

- `deploy/prometheus/prometheus.yml` 的 `rule_files` 注释：写清控制面是人工副本、2026-09-20
  实测落后 2 条、同步方式（重放 + `POST http://127.0.0.1:9091/-/reload`，unit 已开
  `--web.enable-lifecycle`，无需重启）；
- `docs/operations/README.md` 的目录树与「告警规则」两条：把「ADR-0011 **待挂载**」改成
  「人工副本已挂载 + 需人工同步 + ADR-0011 正式挂载仍待落地」；
- `deploy/prometheus/site-alerts.yml` 头部注释：补一句「那一路当前是人工副本，改平台文件后
  需人工重放才生效」。

**历史 note 里的「待挂载」不动**（`docs/notes/**` 是当时事实的记录，按 note 纪律不回溯改写）。

## Alternatives

- **直接把副本同步上去**（`sudo cp` + `POST /-/reload`）：这是**控制面监控配置的生产变更**，
  属 owner 的配置面通道（与 `configure_agents.yml` 同族），不在代码 PR 里顺手做——本单交回
  实测与命令，由 owner 决定何时执行。
- **让 `tools/dev/check-monitoring-assets.py` 覆盖这条路径**：它的映射把
  `/etc/prometheus/rules/alerts-stability-platform.yml` 的来源判成 `site-alerts.yml`（**站点**
  语义），而控制面宿主上该路径装的应是**平台全量文件** ⇒ 在本机它必然报 DRIFT 且「源文件」
  提示误导。改它要先做「本机是站点还是控制面宿主」的判定（host vs site 的身份依据），
  是独立决策面，记入 Revisit。
- **只改 README、不改两个 yml 注释**：改仓库规则的人最先看的就是 `prometheus.yml` 的
  `rule_files` 段（#2488 的教训正是「没有这段，规则装了也不加载」）——漏了它，下一处漂移
  仍会由人工偶然发现。否。

## Verification

- 实测（只读）：`systemctl is-active prometheus` = active；`rule_files` 段实读；
  `ls -la /etc/prometheus/rules/` 与 `wc -l` 对照 `git show origin/main:…`；
  `diff` 规则名集合得缺 2 条；`GET :9091/api/v1/rules` 24 条、无 Pg 规则；
  `GET :9091/api/v1/query?query=stp_pg_schema_error_events` 空结果；
- 文档改动后：三份 YAML 仍可 `yaml.safe_load`（规则数 23 / 2 不变——只加了注释）；
  `tests/test_site_alert_scrape_surface.py` + `tests/test_alert_metric_producers.py` **16 passed**；
  `tests/test_alert_count_claims_are_live.py` + `test_monitoring_asset_drift.py` +
  `test_prometheus_alerts_contract.py` **66 passed**（计数声明类判据未被文案改动破坏）；
  `python scripts/run_gates.py check:quick` → **12 gates 绿**。
- **过程记录**：本单的文档编辑第一次落在**主检出**（上一条命令把 cwd 留在主仓），且该检出的
  HEAD 在别人的分支 `feat/clear-recents-v1` 上——发现后立刻取补丁、还原主检出、在专属
  worktree 重放（当天第二次踩 `worktrees-vanish-commit-early` 的形态，两次都靠「立刻发现」）。

## Revisit

- **正式挂载（ADR-0011）**：人工副本是过渡形态，缺的是「随安装链落地、可复现」的路径。
  在那之前，每次改平台规则都要记得人工重放——本单只把这个义务写进文档，没有机械化。
- **机读判据缺位**：`check-monitoring-assets.py` 的路径映射对**控制面宿主**不适用（见
  Alternatives）；若要机械化，需要先有「本机身份」的判定来源（例如 `install-state.json`
  的存在/内容），再据此选期望源文件。届时本单的三处文案可回落到一句「见工具」。
- **规则计数的两种口径**：仓库文件 23 条（origin/main，含本文写作时）/ 已装副本 21 条 /
  控制面实际加载 24 条（21 + `host-memory-draft.yml` 的 3 条）——三个数字口径不同（文件 vs
  加载 vs 分组）。`test_alert_count_claims_are_live.py` 管的是**文档里的计数声明**，
  本单未在文档里新写任何计数（只写差集 2 条），故不受它约束；若将来要在文档里引用总数，
  先明确是哪一个口径。
