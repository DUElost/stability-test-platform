# 监控漂移检查：`<deploy-user>` 可解析 + skill-usage timer 的 enable 锚（#2866）

Status: implemented
Class: bug-fix

- 日期：2026-09-20
- 关联：`#2866`（本单）、`#2800`（五态与 `source-missing` 的引入面）、`#2785`（该单元与采样项的引入面）、
  `#2881`（同一单元改 root + `--home` 的那一单）

## Decision

两处补齐，都不新建机制：

### 1. `<deploy-user>` 纳入「本机可确定」的占位符

`tools/dev/check-monitoring-assets.py` 新增 `resolve_deploy_user()`——**与 `<deploy-root>`
同法**：探测运行中的 backend unit（`systemctl show stability-backend -p User`），
并在 `expected_text()` / `inspect()` / CLI（`--deploy-user`）/ JSON 输出里串起来。

**为什么必须区分「未知」与「未确定」**：`deploy_user` 为空时**不替换**——占位符残留会让该条目
继续 `SKIPPED`（与探测不到同义），而不是拿字面量去比对判出**假 DRIFT**（开发机没有该 unit）。
即：能确定就真比对，不能确定就如实 skipped，**不猜**。

**实测效果**（本机只读运行）：该条目由 `[SKIP] ← 源含不可由本机确定的占位符 ['<deploy-user>']`
变为 `[OK]`；统计 `match 7 → 8`、`skipped 3 → 2`。此前改这个单元的 `User=`/`ExecStart=`
或环境注入都不会被判定为 DRIFT——「新增采样项 = 新增盲区」。

### 2. S4 的 `enable --now` 补测试锚

`tests/test_site_install.py::test_monitoring_installs_alert_rules_and_guard_units` 的
文件存在列表补 `stp-skill-usage.{service,timer}`，并断言
`systemctl enable --now stp-skill-usage.timer`（照 `stp-script-guard.timer` 的现有形态）。
此前删掉 S4 的这行 enable 不会有门禁变红——而「探针装了没人跑」正是 #2785 批要防的失效模式。

## Alternatives

- **在采样清单里给该条目显式登记「占位符可由本机确定」**：那要再维护一张登记表，且它只回答
  「能不能确定」、不提供取值 ⇒ 仍要另一处实现替换。直接探测（本单做法）不需要登记。否。
- **探测不到时按字面量比对**：开发机/新克隆上必然判 DRIFT（假漂移），与 #2800 引入
  `source-missing` 是同一类教训的反面。否。
- **把站点自定义的 `deploy-user` 写死进检查器**：站点各不同，写死等于把「不可确定」伪装成
  「已确定」。否。
- **`<site-id>` / `<prometheus-port>` 一并解析**：它们**本来就不可由本机唯一确定**
  （站点自定），保持 skipped 是正确语义。否（本单只动能探测的那一个）。

## Verification

- 实测（本机，只读）：`venv/bin/python tools/dev/check-monitoring-assets.py` → 该条目
  `[OK]`，统计 match 8 / drift 1（剩下的 drift 是 #2880 那条规则副本，与本单无关）；
- `python -m pytest tests/test_monitoring_asset_drift.py tests/test_site_install.py -q` →
  **96 passed**（新增：`<deploy-user>` 三面用例——已知 ⇒ match、已知但副本是他用户 ⇒ **真 DRIFT**、
  未知 ⇒ skipped）；
- **3 处定向变异逐条回退即红**：不再替换 `<deploy-user>` → 1 failed；未知也替换（猜值）→
  1 failed；S4 把 `enable --now` 改成只查状态 → 1 failed；
- `python scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- **还剩两个不可解析占位符**（`<site-id>` / `<prometheus-port>`）：它们是**站点自定**值，
  本机无法唯一确定 ⇒ 保持 `skipped` 是正确语义，不是本单的遗漏。若将来出现**第三个可由本机
  事实探到的**占位符，照 `resolve_deploy_user()` 的形状扩一个 `resolve_*`，别在清单里加白名单。
- **探测依赖 backend unit 存在**：开发机/CI 上该条恒 `skipped`——这是**有意**的（不猜），
  但它意味着本单的收益只在实际部署形态（控制面宿主）上兑现。若要 CI 也覆盖，需要一个
  注入 `--deploy-user` 的用例（已有：测试就是走这条路径）。
- **`stp-skill-usage.service` 本轮之后仍是 `[OK]`**：说明已装副本与仓库一致；但注意 #2881
  那一单会改该单元（root + `--home`）——合入后本检查会在部署侧如实报 DRIFT 直到重放，
  这正是本单要恢复的能力（而不是继续 SKIP）。
