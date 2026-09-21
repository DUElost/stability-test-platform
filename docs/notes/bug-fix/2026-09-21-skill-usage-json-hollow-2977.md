# skill 用量 `--json` 漏传 strong_source_present：缺源 hollow 假阳（#2977）

Status: implemented
Class: bug-fix

## Decision

三处同修，消除「表格不判洞 / JSON 判洞 / 裸告警叠警」分裂：

1. **`skill_usage_report.py` `--json` 分支**：`is_hollow(..., strong_source_present=strong_present)`，
   与表格路径同一判据（#2851 语义）。
2. **`skill_usage_probe.summarize`**：缺源时把 `hollow` **置 0**（不再原样带出假值），
   只留 `unknown=1`——与注释「告警只看 unknown」一致。
3. **告警**：`StabilitySkillUsageHollow` 表达式加 `and stp_skill_usage_unknown == 0`
   （平台全量 + site 副本），防御探针旧二进制尚未热更时的叠警。

## Alternatives

- **只改 JSON 分支**：能修根因，但已写出的假 hollow + 裸规则仍会叠警；探针与规则
  仍应自洽。
- **只改告警表达式**：治标不治本——JSON 仍报 hollow>0，人工读指标仍被误导。

## Verification

- `python -m pytest tests/test_skill_usage_report.py tests/test_skill_usage_probe.py -q`
- promtool 场景：`hollow>0 ∧ unknown=1` 不得触发 Hollow（`alerts-stability-platform.test.yml`）
- `python scripts/run_gates.py check:quick`

## Revisit

合入后控制面需同步 alerts 副本（若用 site 安装器/漂移检测）；探针 binary 随部署树
更新后，缺源站点应只见 Untrusted、不见 Hollow。
