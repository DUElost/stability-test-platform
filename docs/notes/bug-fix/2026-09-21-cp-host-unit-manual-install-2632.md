# 控制面宿主单元的手工安装必须可照做可验证：stp-pg-guard 漏装 → 告警恒响三天（#2632）

Status: implemented
Class: bug-fix

## Decision

**现场**（2026-09-21 21:3x，本机即控制面宿主，全程只读 + 一次单元安装）：

- `systemctl is-enabled/is-active stp-pg-guard.{timer,service}` → **`not-found`**：这个单元
  从未在本机安装过，`/var/lib/prometheus/node-exporter/` 里也没有 `stp-pg-guard.prom`；
- 同日 19:56 平台规则副本重放后（#2984/PR #3021），规则里那条
  `expr: stp_pg_schema_error_events >= 5 or time() - stp_pg_guard_last_run > 3600 or absent(stp_pg_guard_last_run)`
  立刻**恒响**：`StabilityPgSchemaGuessing` state=firing（`absent()` 兜底把「生产者不在场」
  判成 firing——这是判据**设计正确**的表现，问题在没人看）。
- 换言之：#2632 的「观测先行」在仓库侧早就做完了（工具 + 单元 + 规则 + 文档 + 一条 Agent Note
  `2026-09-18-pg-schema-guess-observability-2632.md`），**缺的只是「在控制面宿主上把它装上」这一步**，
  而这一步在 `docs/operations/installation.md` 里只是一句括号里的「按控制面宿主手工安装」——
  没有命令、没有验证锚点，于是三天没人做，告警一直响。

**本次交付**：

1. **落地**（ops，非仓库变更）：按单元头自述的手工步骤在本机渲染并安装
   `stp-pg-guard.{service,timer}`（`sed s|<deploy-root>|/home/debian13/stability-test-platform|g`
   → `/etc/systemd/system/` → `daemon-reload` → `enable --now` 定时器 → 手动跑一次服务出首份指标）。
   验证三件套全绿：`Result=success` / `stp-pg-guard.prom` 902 B / `:9100` 暴露
   `stp_pg_guard_last_run`；`StabilityPgSchemaGuessing` 在 `for` 窗内 **firing → inactive**。
2. **文档**（本 PR）：把 `docs/operations/installation.md` 的那句括注扩成**可照做的步骤 +
   三件套验证**，并写明「漏装的唯一表现是告警恒响」，以及 `absent` 兜底型判据的读法
   （「绿」只在生产者真在跑时才代表「没有猜 schema」）。

**边界**：#2632 的另外两项（专用只读角色的凭据面、`stp_test` 连库来源排查）**不在本 PR**：
前者是生产库权限变更、需要 owner/安全签字；后者是启动时那次现象（09-17）的根因排查，
本文没有新增证据。本 PR 只收口「观测先行的落地缺口」。

## Alternatives

- **只把单元装上、不动文档**：放弃。下一个控制面宿主还会重复同一空转——这次缺的不是
  人的勤快，而是把括注变成步骤；文档是唯一能在下一台机器上生效的载体。
- **把 `stp-pg-guard` 加进站点安装清单，让安装器代装**：放弃。它读的是本机 PG 服务日志，
  站点上装了扫不到东西（#2788 已纠正过这个表述），代装会把「装了也没用」变成常态。
- **靠告警自己发现**：这条**确实生效了**（firing 就是它报的），但发现后无人处置等于零；
  「检测正确」不能替代「安装步骤可照做」。
- **给漂移检测器（`check-monitoring-assets.py`）加这一项**：本次不做。该检查模型的每一项
  是「站点已装副本」，加入一个站点面之外的宿主单元会污染其语义（要么误报 absent、要么得先
  引入 host-role 维度）。当前的可发现性已由那条 `absent()` 告警提供。

## Verification

- 只读取证：`systemctl is-*`（not-found）、`ls /var/lib/prometheus/node-exporter/`（无
  `stp-pg-guard.prom`）、Prometheus `127.0.0.1:9091`（**注意实际端口是 9091，不是 9090**）
  `/api/v1/rules` → `state=firing`；
- 安装前先验工具：`pg_error_guard.py --self-test` 通过、`--dry-run --json` 实读 PG 日志
  返回 `{"undefined_table":0,...}`（当前窗口 0 命中）；
- 安装后：`systemctl show -p Result` = success、指标文件 902 B（5 条 `stp_pg_*`）、
  `:9100/metrics | grep stp_pg_guard_last_run` 命中、`/api/v1/rules` →
  `StabilityPgSchemaGuessing state=inactive`、活动告警数 20 → 18；
- 装的是 `origin/main` 上的单元与工具（`pg_error_guard.py` 的 sys.path 自举修复由 #2973/PR #3009
  于 12:40 合入，已确认在 main 上——**先确认这一点再装**，否则装上去的就是个 rc=1 的假生产者）。

## Revisit

- 下一台控制面宿主上线时，按本节步骤安装并跑完三件套验证——这条应该进宿主接入 SOP
  （`agent-host-onboard` / 控制面部署 skill）而不是只躺在 installation.md。
- #2632 的另两项（专用只读角色 / `stp_test` 连库来源）仍开放；本次只关了落地缺口。
- 平台规则副本重放（#2984 族）每次都会让「生产者不在场」的告警从不存在变成 firing——
  重放之后应顺带核一遍**每条规则的分子/分母生产者是否都在跑**（本次就是这类）。
