# oversized 目录计数指标的 HELP 谎称跨 job 唯一：口径修正 + 三处静态对拍（#2640）

Status: implemented
Class: bug-fix

- 日期：2026-09-18
- 关联：`#2640`（本单）、`#2252`（尺寸防线，该指标服务的对象）、`#2394`（指标引入批）、
  `#2639`/PR #2688（`tools/dev/source_anchor.py`——本单是它的第一个新用例）、`#1707`（根 `tests/` 离线子集判据）

## Decision

**判据读码结论（与 issue 一致，逐处复核）**：`stability_reconciler_dirs_oversized_skipped_total`
的 HELP 写「cumulative unique dirs」，而实现是 Σ_per_job(该 job 内唯一目录数)：

- `backend/agent/aee/unisoc_reconciler.py:616` → `self.stats.dirs_oversized_skipped = len(self._oversized_seen)`；
- `backend/agent/aee/unisoc_reconciler.py:161-163` → `self._oversized_seen: Set[str] = set()` **在实例 __init__ 里新建**，
  reconciler 每 job 一份（`job_session.py`），所以去重域只在 job 内；
- `backend/services/agent_completion.py:184-186` → job 终态桥接 `record_reconciler_dirs_oversized_skipped(host, amount=oversized)`
  每个完成 job 累加一次。

⇒ 同一目录在 N 个 job 里被降级就计 N 次。**采建议 1（改 HELP 口径），不采建议 2（改实现）**：
唯一性要跨 job 成立就得把集合升成进程级并考虑内存/持久化，而这个指标的用途是「降级发生过没有、
规模趋势如何」——按 job 求和恰好是有用口径，问题只在 HELP 把它说成了「唯一目录数」。
issue 的这条判断（「不建议为观测口径付这个代价」）我复核后同意，不是照抄。

顺手核实下游有没有被误导：`rg stability_reconciler_dirs_oversized_skipped deploy/ docs/`（除 Note）
**零引用**——告警只用了同族的 `..._dirs_abandoned_total`，而它的 HELP 没声称 unique/cumulative
（与 issue 结论一致，不改）。所以这是一处**语义面失真**而非数据面缺陷。

### 判据：三处静态对拍，而不是只改一行文案

只改文案属于止血——它下次还会以同样的形态漂移（实现改了、HELP 没跟上，或反之）。
`tests/test_unisoc_dir_metric_semantics.py` 把三方钉成互相自洽：

| 判据 | 钉住的事实 |
|---|---|
| HELP 含 `per-job`、不含 `cumulative unique dirs` | 读者拿到的口径 |
| `len(self._oversized_seen)` + 集合在 `__init__` 新建 | 唯一性只在 job 内的**实现根据** |
| 桥接 `amount=oversized` 每完成 job 一次 | 「per-job 求和」的落点 |
| 记录函数 docstring 含「唯一性只在 job 内」 | 读码路径与 HELP 同一口径 |
| Counter 首参仍是那个导出指标名 | 改名后对拍不会查错对象 |

- HELP 用 **AST 取字面量**（不 `import backend.core.metrics`）：根 `tests/` 要守 #1707 的离线子集，
  而该模块导入期就解析配置并注册指标。
- 三处都走 `tools.dev.source_anchor.SourceGuard`（#2639，本单是它落地后的第一个新用例）：
  实现搬家时报「用例已过期（锚点漂移）」，口径被改错时报「防线回归」，两种红不再混成一类。
  同时它满足 #2639 的棘轮（按真实 import 判已迁移），`tests/test_source_scan_anchor_ratchet.py` 仍绿。

## Alternatives

- **把集合升成进程级去重（issue 建议 2）**：被否（与 issue 同判断，理由如上）。若将来真需要
  「某 host 有多少个被降级目录」，正确形态是**新增一个 per-job 末拍快照 gauge**，
  而不是把 Counter 改成跨 job 去重（那会让 `increase()` 的语义随 job 边界消失）。
- **只改 HELP，不加判据**：被否。这正是 #2640 之所以能存在的原因——HELP 与实现之间没有任何约束。
- **在 `backend/tests/api/test_agent_api_watcher.py` 里加运行时断言**（起一次桥接看计数）：
  被否。运行时能证明的是「inc(amount) 被调用」，证不了「HELP 文字与口径一致」；
  而且那批用例只在夜间 `backend-test` 跑（#2639 刚为此立过判据）。
- **给全仓 HELP 文本做「含 cumulative 必须真是跨进程单调」的通用门禁**：被否——判据无法机械化，
  会产出一堆「红灯但不可修」的死结（同类教训见 `check-script-version-immutability` 的排除面）。
- **把 docstring 判据也做成否定断言**（禁止出现「不能当目录数读」的反面说法）：被否，
  否定式对文案没有意义，正向钉一句结论即可。

## Verification

只列实跑命令与结果。

- `env -u DATABASE_URL …python -m pytest tests/test_unisoc_dir_metric_semantics.py -q` → **4 passed**。
- 同目录连带复跑 `tests/test_source_scan_anchor_ratchet.py tests/test_source_anchor_helper.py` →
  **17 passed**（新用例被棘轮正确豁免：它真的 `from tools.dev.source_anchor import …`）。
- 判据变异 **7 条全部变红**，还原后基线 4 passed：
  V1 HELP 回潮成 `cumulative unique dirs` → `FormRegression`；V2 HELP 丢掉 `per-job` → `FormRegression`；
  V3 生产侧聚合式改成函数调用（`len(self._oversized_seen)` 消失）→ `AnchorDrift`；
  V4 桥接入参形态改名 → `FormRegression`（锚点仍在，属真实形态变化——分类正确）；
  V5 导出指标名改名 → 配对校验先红（`AssertionError`，指路「两个常量一起改」）；
  V6c 记录函数 docstring 删掉「唯一性只在 job 内」→ `FormRegression`；
  V7 桥接读 `stats.get("dirs_oversized_skipped")` 那行改用常量 → `AnchorDrift`。
  前两轮变异（V4 预期写成 AnchorDrift、V6 打错靶）已如实纠正为上面两条结论——
  变异打偏不是守卫的问题，是探针的问题，故换靶复跑。
- `python -m ruff check tests/test_unisoc_dir_metric_semantics.py backend/core/metrics.py` → All checks passed。
- `python tools/dev/check_governance_surface.py --check` → `[OK] 治理面结构检查通过（阻塞项全绿：S1–S14、S5x）`。
- `python scripts/run_gates.py check:quick` / `check:pr`：提交后在干净树上跑，结果写进 PR 描述。
- pending：无生产侧行为变更 ⇒ 不需要看板/告警回归；`increase()` 类查询若将来引用该指标，
  需按 per-job 求和口径写表达式（HELP 已写明）。

## Revisit

- **只能静态对拍，做不到运行时等价**：HELP 是给人读的文本，运行时没有任何东西能证它等于实现。
  现在的判据把「三方自洽」钉住，但仍存在「三方一起被改成同一句假话」的可能——那属评审面。
- **needle 是文本匹配**（#2639 的已知代价）：HELP 若被折行成多个字面量拼接，AST 取到的
  仍是折叠后的单串（本单已验证）；但若有人把 `per-job` 改写成 `per job`，会判 `FormRegression`
  而不是静默通过——假阳性方向，逼人来读一眼，可接受。
- **同族指标的下一步**：`..._dirs_abandoned_total` 与 `..._unresolved_dirs` 现在没有等价对拍。
  本文件的形状可以直接复制；是否值得做取决于是否再出现口径争议（不预先铺，避免为对齐而对齐）。
- **是否需要一个真正的 per-job 快照 gauge**：如果 owner 认为「某 host 有多少目录被降级」是
  运维必需读数，那才需要新指标（终态方案），届时本 HELP 要再改一次指向它。
