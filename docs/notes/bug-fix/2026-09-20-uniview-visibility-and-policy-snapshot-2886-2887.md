# #2886 #2887 agent 上报面：UNIVIEW 无消费方可见性 + policy_snapshot 与订阅面同源

Status: implemented
Class: bug-fix

## Decision

两个派生自 #1998 唤醒层的「上报面失真」缺陷，一处根因是「快照/标记只在启动时写一次、运行期变化不回写」，一并收口：

- **#2887（上报面与实际订阅相反）**：`JobSessionSummary.policy_snapshot` 唯一赋值点在
  `JobSession.__init__`（`job_session.py`），而 UNISOC 的 policy 注入在
  `__enter__ → _maybe_apply_unisoc_inotifyd_paths` 里**原地改** `self._policy`（
  `paths["UNIVIEW"]` + `required_categories=["UNIVIEW"]`）。dataclass 快照是改前的
  dict 副本，不自动同步 → 开启 `STP_WATCHER_UNISOC_INOTIFYD` 且平台=UNISOC 时，
  上报的 required_categories 仍是 MTK 默认（AEE/VENDOR_AEE），与实际订阅 ["UNIVIEW"] 相反。
  修法＝注入成功后刷新 `self._summary.policy_snapshot = self._policy.to_dict()`，
  保证「上报面 ≡ 实际订阅面」同源。并把 `policy_snapshot` 纳入
  `to_complete_payload()`——此前它从未随 complete 通道带出，刷新了平台也看不到；
  控制面 `watcher_summary` 为 `Dict[str,Any]` 未消费该键，仅为运维可观测（#96 / #2394-③
  同构先例：payload 带诊断键、后端只读不写业务列）。
- **#2886（覆盖归零不可见）**：`_route_unisoc_wake` 在 `wake is None`（未接线，或
  reconciler 连续错误**自关闭**经 `_clear_unisoc_wake` 清除后）时，UNIVIEW 事件仍恒
  消费、不 emit——这是有意设计（reconciler 独占信号/DLE 写入，避免双写），本单不主张
  回落 emit（属语义裁决）。主张的是**静默性**：reconciler 死亡 ⇒ 该 job 余下生命周期
  UNIVIEW 覆盖为 0，此前只有 `logger.debug` 一行，#806 的可见性兜底对这条路径结构性
  无效（它复位的是 AEE/VENDOR_AEE 的 emit 抑制位，UNIVIEW 在 emit 前就被 return）。
  两条非 debug 出口：① watcher 计数 `unisoc_wake_no_consumer` 进 `WatcherStats`
  （随 `summary.watcher_stats` 回流平台），首见记一次 WARNING、后续只计数不刷屏；
  ② `JobSessionSummary.platform_reconciler_shutdown: bool` 默认 False，自关闭回调
  置位（与 `platform_reconciler` 在位标记配套——后者只写一次、停摆不回写）。

## Alternatives

- #2887 备选「把 summary 构造整体推迟到注入之后」：改动 `__init__`/`__enter__` 时序、
  且 `policy_snapshot` 之外字段也依赖注入前 handle，牵连面大于「注入点单行刷新」，弃。
- #2886 备选「UNIVIEW 在 wake 缺失时回落 emit」：正是本单边界声明排除的语义决策
  （reconciler 双写代价，`#740` 烧提取预算），留给 owner，不在本修复内。
- 告警用 metric 还是日志：二者都做——计数给平台侧聚合/告警规则一个可查询量，
  WARNING 给现场一次可 grep 的信号；纯 debug 是本轮被否的现状。

## Verification

- 受影响用例 `backend/agent/tests/test_unisoc_wake.py` + `test_job_session.py`：38 passed。
  新增/加固断言：`_bare_session` 铺 `JobSessionSummary`（与生产 __init__ 同形）验证
  注入后 `policy_snapshot["required_categories"]==["UNIVIEW"]`；gate-off / 非 UNISOC
  两路验证快照不被污染；`test_uniview_without_consumer_warns_once_and_counts` 验证
  计数=3、to_dict 含键、WARNING 恰一次；`test_reconciler_self_shutdown_wiring` 验证
  停摆位置位且随 `to_complete_payload()` 带出；`test_to_complete_payload_shape` 补
  `policy_snapshot`/`platform_reconciler_shutdown` 两键（精确键集断言会因新增键而失败）。
- agent 全量 `backend/agent/tests`：2065 passed。
- 变异自证（下一步）：撤销 #2887 刷新行 ⇒ gate-on 用例必红；撤销 #2886 计数 ⇒ warns_once 必红。

## Revisit

- 若 owner 裁决「reconciler 停摆时 UNIVIEW 应回落 emit」，本单的非 debug 出口仍保留
  （可发现性独立于回落决策）；届时 `_route_unisoc_wake` 需加 active-flag 判据分流。
- 控制面若将来消费 `policy_snapshot`/`platform_reconciler_shutdown`（如落 job 列或
  桥接指标），需同步 `apply_watcher_summary` 与契约，避免 payload 键与 DB 列漂移。
