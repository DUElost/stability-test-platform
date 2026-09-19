# 守卫可信性批：四处「失败被读成通过」的收口（#2797 / #2798 / #2800 / #2801）

Status: implemented
Class: bug-fix

## Decision

同一问题面（守卫自身失真 → 假绿）的四个独立点，一次批量收口：

1. **#2797（rc=1 伪装）**：`tools/dev/script_guard_probe.py::summarize` 对 `GUARD_DUE=1`
   增加形态判据——**payload 是否带 `--guard --json` 约定的 `guard` 块**。不带 ⇒ 判据进程
   在 `main()` 的 try 之前就死了（module 级 `resolve_database_url()` / `create_engine`，
   解释器默认退出码同为 1），折成 `broken=1` + 任务 exit 1。带 ⇒ 才是真「有活要干」
   （任务 exit 0，数量进 `due`）。文档串的退出码表同步标注第二种来源。
2. **#2798（相对导入失明）**：`tests/test_agent_import_boundary.py` 的检测器按 PEP 328
   解析 `ast.ImportFrom.level`（新增 `_module_name_for` / `_resolve_import_from`，签名加
   可选 `module=`；越过顶层包不可解析时 fail-safe 返回空）。真实逃逸
   `backend/agent/aee/reconciler.py:81` 的 `from ...core.metrics import (…)` 现在解析为
   `backend.core.metrics`——**裁决为登记豁免**：模块体纯（`prometheus_client` 可选，无
   DB/Redis），消费方 `try/except + no-op` 兜底，与既有两条 `backend.core.*` 豁免同口径；
   豁免理由里写明「`backend.core` 包 init 会拉 DB、agent 主机上走兜底」这一事实。
3. **#2800（源缺失记 SKIPPED）**：`tools/dev/check-monitoring-assets.py` 新增第五态
   `source-missing`（清单指向的仓库源文件不存在）。`summarize`：源缺失 ⇒ `EXIT_UNKNOWN`；
   全部条目非判定（source-missing/skipped）⇒ `EXIT_UNKNOWN`；**全 ABSENT 仍 `EXIT_OK`**
   （本站未装监控栈是确定事实，不是判不出）。CLI 出 `MISS ` 行与统计，`check-deploy-source.sh`
   的 WARN 过筛同步加 `MISS `（否则源缺失在 WARN 里不可见）。
4. **#2801（`on:` 块不校验）**：`tools/dev/check_governance_surface.py` 新增
   `_s5x_parse_pr_trigger`（标量 / flow 列表 / 映射块三种写法），`_s5x_pr_reachable` 改为
   `pr_trigger` 必填 kwarg：workflow 级不含 `pull_request` **或不可解析（None）**时任何
   job 都判「不可证明可达」（红）。报错串带上触发块状态便于归因；S5x 夹具补 `on:` 块并新增
   4 条自测（含 #2801 原始形态：无 `if` 锚点 + workflow 无 PR 触发）。

## Alternatives

- **#2797 改判据侧（把 module 级 DB 解析挪进 `main()` 的 try、直接退 3）**：弃——
  判据文件自述「bootstrap 之前 import 期就炸，任何 wrapper 都抓不到 ⇒ 两者必须同时存在」，
  死亡形态不可穷举（解释器缺失、依赖缺失、路径形态、超时已在探针侧各有分支），
  消费侧按「能不能读到 payload」判定才是闭口。
- **#2798 删除 agent 侧对 `core.metrics` 的依赖**：方向正确但超出本单——归档计划
  （watcher-consolidate-aee）已记「Agent 无 /metrics 端点 ⇒ 该指标在 agent 侧等同 dead」，
  真正该做的是中心侧补记（`complete_job` 时带 N）。本次只做**可见化 + 显式裁决**，
  dead metric 的处置留给独立单（豁免理由已写明该事实，便于后续收口时定位）。
- **#2801 把「不可解析」当绿（保守放行）**：弃——本文件判据一贯「宁愿报红不猜绿」
  （#2445 的 `_s5x_pr_reachable` 复合表达式即返回 None）。解析失败意味着 CI 触发面
  不可证，与「确认可达」不是一回事。
- **#2800 源缺失判 `EXIT_DRIFT`**：弃——漂移指「站点副本与仓库不一致」；源缺失时
  根本无从比对，报 2（无从判定）才与文件自身契约（「读不到→2」）一致，也不会把
  「修清单」与「重跑安装」两种处置混成一个红灯。

## Verification

- `tests/test_script_guard_probe.py` → **26 passed**（新增 rc=1 两来源区分 + main 级 broken 落盘）；
  反向验证：把 `guard is None` 分支改回 `due=1` ⇒ **4 failed**（含参数化两例）。
- `tests/test_agent_import_boundary.py` → **5 passed**（新增相对导入解析三断言：普通模块 /
  `__init__` 包语义 / 越过顶层 fail-safe）；主扫描现在真正看见 reconciler 的逃逸
  （`test_shared_allowlist_entries_are_still_used` 由「失效条目」转「在用」即证）。
- `tests/test_monitoring_asset_drift.py` → **25 passed**（新增源缺失→2、全非判定→2、
  全 ABSENT→0）；反向验证：`SOURCE_MISSING` 改回 `SKIPPED` ⇒ **1 failed**（命中新断言）。
- `tools/dev/check_governance_surface.py --self-test` 通过；反向验证：`pr_trigger` 前置
  短路去掉 ⇒ **自测失败 5 项**；真跑治理面检查 `[OK] S1–S15、S5x`。
- **真机 E2E（#2797 原形态复现）**：`env -u DATABASE_URL` 下跑探针 ⇒ 判据在 import 期
  抛 `RuntimeError: DATABASE_URL is not set`、无 payload；探针落
  `stp_script_guard_broken 1`、`due 0`、任务 exit 1（修复前会落 `due>=1`、exit 0）。
- `python scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- #2798 的 `backend.core.metrics` 豁免：若将来 agent 侧真起 `/metrics` 端点、或中心侧
  补记落地，重新裁决该条目（可能改为「中心记 N、agent 侧删除」）。
- #2801 的 `on:` 解析只覆盖三种常见写法；若 ci.yml 改用可复用 workflow（`workflow_call`）
  或动态触发，需扩展解析并补自测。
- #2797：若判据侧将来把 module 级 DB 解析收进 `main()`（可直接退 3），本探针的
  「无 payload ⇒ broken」判据仍应保留——它覆盖的是死亡形态，不是某一种实现。
