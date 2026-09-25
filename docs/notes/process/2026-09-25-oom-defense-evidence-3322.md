# OOM 防线取运行时证据：earlyoom 会选错目标（已修）+ 四条余量规则补场景（#3322 / #3318）

Status: implemented
Class: process

## Decision

授权做一次"能安全做的演练"，目标是给 09-23 之后这条防线补**运行时证据**（此前只有装配期
判据）。做法是把演练拆成两半，只做不需要把机器推向死亡的那一半：

**① 选目标：用 `--dryrun` 观测器取证，不制造死亡。** 另起一个实例、用与生产**完全相同**的
`--avoid/--prefer`，只把阈值放宽（`-m 90,85 -s 99,99`）并带 `--dryrun`（永不发信号），场上放
一个 `python` 失控体，读它"会杀谁"。结果推翻了我 09-23 的判断：

| 观测 | 结果 |
|---|---|
| 默认（按 kernel `oom_score` 排序） | 反复选中 **1.18 GiB 的 `yandex_browser`**（oom_score **1184**），对场上 2 GiB 的 `python` 失控体无动于衷 |
| 加 `--sort-by-rss` | 选中真目标 `python3 hog.py`（VmRSS 5 GiB）✅ |

⇒ 这解释了 09-23 13:42 死前它为什么盯上 **24 MiB 的 zcode（oom_score 1172）** 而不是 15.3 GiB
的元凶：**不是 dryrun 一个原因，排序键本身也是原因**——桌面/浏览器系进程带正的
`oom_score_adj`，分数被抬高。我 09-23 只补了 `--prefer python`，那**必要但不充分**：prefer 只
决定"优先候选集"，集内仍按分数排。已把 `--sort-by-rss` 同时落到宿主与 `earlyoom.default`。

**② 告警层：用 promtool 场景代替实弹。** 四条余量规则此前**零场景覆盖**（#3233 只给它自己的
磁盘三条写了场景），CI 全程静默。补 `alerts-host-resources.test.yml` 第 ②③ 段：正例（真机
13:40→13:42 的数字造序列）+ 负例（稳态高 majflt 但余量足，必须闭嘴），并新增
`tests/test_host_rule_scenario_coverage_3322.py` 把"角色文件每条规则都要有场景"焊成判据。

**不做的一半（明确拒绝，不是遗漏）**：验证"真击杀"与"真复位"需要同时把 avail 与 SwapFree 压到
earlyoom 阈值以下，即**在这台同机承载 PostgreSQL + NFS 导出 + 48 台 agent 心跳的宿主机上重造
09-23 的失速**；唯一兜底是 30s 硬复位（= 非受控重启 + PG 崩溃恢复 + 在跑任务受损）。收益
（拿到一条 journal 证据）与代价不对称，**拒做**；要取得该证据的正当路径是 #3322 里记的
"低峰 + 提前通知 + 只到 SIGTERM 档"，由 owner 拍风险。

## Alternatives

- **直接调 `-m/-s` 让生产 earlyoom 在高位杀一次**：否决——那会让它真杀场上最大的
  prefer 命中者（可能是别人的 AI 会话/浏览器，不是我的失控体），①的观测已证明它选不准。
- **把 `--prefer` 里加更宽正则代替 `--sort-by-rss`**：否决——问题在**排序键**不在匹配集。
- **只做 promtool、不做 dryrun 观测**：否决——那会保留"以为撤了 dryrun 就有防线"的错觉。
- **豁免登记式覆盖**（只测磁盘三条、内存四条继续裸奔）：否决，与 #3106「探针建而不用」、
  #2488「规则一条没上线」同形。

## Verification

```bash
# ① 选目标（全程 --dryrun，未发任何信号；观测完回收失控体）
/usr/bin/earlyoom -r 3 -m 90,85 -s 99,99 --dryrun --avoid '<生产同款>' --prefer '<生产同款>'
  → 默认：sending SIGKILL to process … "yandex_browser": oom_score 1184, VmRSS 1183 MiB
  → --sort-by-rss：… "python3": … VmRSS 5057 MiB, cmdline "python3 hog.py"
sudo systemctl restart earlyoom            # 生效；击杀次数仍应为 0（只改选谁）
tr '\0' ' ' </proc/$(pgrep -x earlyoom)/cmdline | grep -o -- --sort-by-rss   # 有输出

# ② 场景
promtool test rules deploy/prometheus/alerts-host-resources.test.yml   → SUCCESS
promtool check rules deploy/prometheus/alerts-host-resources.yml       → SUCCESS: 7 rules found
pytest tests/test_host_storage_alerts_3233.py tests/test_prometheus_alerts_contract.py
       tests/test_monitoring_asset_drift.py                            → 86 passed
pytest tests/test_host_rule_scenario_coverage_3322.py                  → 2 passed
# 反证（重要，第一次做错了）：摘掉某告警的**全部**场景引用 → 判红并点名；
# 只摘正例不摘负例 → 仍绿（我第一版反证就犯了这个错，反证本身要能证伪）。还原后 → 2 passed。

# 装配期既有判据（09-23 起在跑，本次未回归）
systemctl show -p RuntimeWatchdogUSec --value → 30s；/dev/watchdog0 由 PID1 持有
check-monitoring-assets.py → match 12 · drift 0（对 origin/main）
```

未验证项（诚实口径）：earlyoom **真实击杀 0 次**、watchdog **从未真复位**、四条余量告警**至今
0 次触发**。前两项是刻意不做；第三项只说明两天没跌到阈值，**不等于判据可用**——可用性的证据
现在是 promtool 场景 + 09-23 真数回测（`<8 GiB` 在 10020 基线分钟里 0 误报、13:42 即响）。

## Revisit

- #3318：8 份存量副本的部署根重放（我已按权威值重放并复测 drift 0；若下次部署换根，同口径再来一遍）。
- #3223 账 1：给 `wait_system_ready` 加**迭代上界**（引信仍在，需脚本新版本，ADR-0039）。
- 若将来要取"真击杀"证据：先确认 `--sort-by-rss` 已在（否则第一次演练会杀错人），并在演练前
  把 `--avoid` 复核一遍——本次就发现 09-14 原表漏了 `redis-server`/`node_exporter`。
