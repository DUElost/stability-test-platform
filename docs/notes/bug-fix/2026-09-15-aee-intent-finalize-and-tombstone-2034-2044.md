# AEE 意图簿两处 fail-open：占位写失败不再 finalize（#2044）+ 跨前缀墓碑回查真正可达（#2034）

Status: implemented
Class: bug-fix

## Decision

两单同属一个不变量：**「意图先于效果落盘」，且已落盘的意图不能被提前抹掉**。

- **#2044 占位写失败 = 该条目不 finalize**（`backend/agent/aee/processor.py`
  `_finalize_processed_entry` + `backend/agent/aee/reconciler.py`
  `_record_intent_placeholder`）：占位钩子不再内部吞异常，失败原样上抛；processor
  捕获后**不推进 `processed`、不调 `on_new_entry`、不计 `pulled`**，而是把该行以
  `retry_count + 1` 与 `last_error="emit_intent_placeholder_failed: <ExcType>"`
  放回 pending，并在 `result.errors` 记一条。旧形态是「两层吞 + processed 照常
  前进」：意图簿里没有该行的记录，`sweep` 又只遍历既有意图簿，两者叠加即静默永久丢失。
- **重试是廉价且有界的**：下一拍走「本地目录已存在 → strict verify → finalize」，
  不重新 pull；用尽 `cfg.pull_retry_limit` 后由既有
  `on_pull_failed(exhausted=True)` 通道带出真因（`error` 取 `task["last_error"]`），
  行落 processed 并留 ERROR 日志——丢失不再静默，也不需要新增计数器字段。
- **#2034 成因 A：回查判据改为「本前缀没有可用幂等键」**（`_handle_new_entry`：
  `record is None or not (record.get("done") or record.get("seq_no") is not None)`）。
  processor 总在 `on_new_entry` 之前先落占位，且占位与 emit 用同一个
  `state_key_prefix`（`_state_prefix_for` 透传），本前缀簿因此必然已有一条未 done
  的占位——#1862 那句 `if record is None` 在生产路径上永不成立，回查是死代码。
- **#2034 成因 B：baseline 墓碑按 runtime processed 清理**（`_sweep_emit_intents`：
  非 runtime 前缀的 `done` 记录改查
  `state_key(serial, aee_type, prefix=self._state_prefix)` 的 processed，即
  `_merge_baseline_into_runtime_processed` 的落点；runtime 簿仍按自身 processed）。
  `tick_once` 是先 sweep 再拉取，按本前缀判定会在崩溃后的第 1 个 tick 就删掉墓碑
  ——而它唯一服务的对象（runtime 重拉）发生在之后。
- 回归测试改为**走 processor 路径**（`_setup_pdl_stubs` + `_runtime_hooks` 复刻
  `tick_once` 的 runtime 接线），不再只直调 `_handle_new_entry`；新增 5 例。

## Alternatives

- **#2044 方案 2：让 sweep 扫「processed − 意图簿」差集**（issue 内的备选 2）：放弃。
  processed 已前进而意图不存在时，`parsed` / `output_subdir` / `detected_at` 均已不可得，
  等于要求把意图簿的载荷复制进 processed 集合——成本高且制造第二个事实源。
- **#2044 新增 `ReconcilerStats` 字段**（如 `emit_intent_placeholder_failures`）：放弃。
  `to_dict()` 会进 JobSession summary → 前端 `frontend/src/utils/api/types.ts` 需同步，
  而前端面正被在窗 Execution（#2051-2054）占用；`signals_dropped` 的语义是「已丢弃」，
  中间态失败计入它等于制造假告警。
- **#2044 让异常直接穿出 `_finalize_processed_entry`**（整轮 tick 失败）：放弃。
  一次瞬时写失败会被放大成整设备本轮处理中断——#78/#72 已为此把 `PermissionError`
  收敛成 `errors` 项而非抛出，同一教训不再犯。
- **#2034 只修成因 A**：放弃。成因 B 独立成立（sweep 先于 pull），单修 A 后墓碑仍在
  被引用之前就没了，窗口原样留着。
- **给 baseline 墓碑加 TTL / 容量上限**：暂不做。当前判据下墓碑只在「runtime 尚未
  finalize」期间留存，正常路径存活 ≤ 1 个 tick；先留 Revisit 钩子。

## Verification

- 新增 5 例（`backend/agent/tests/test_aee_emit_intent_1719.py`）：
  `test_intent_placeholder_failure_does_not_finalize_entry`、
  `test_intent_placeholder_failure_retries_then_emits_exactly_once`、
  `test_intent_placeholder_retries_are_bounded_and_report_true_reason`、
  `test_cross_prefix_tombstone_survives_the_runtime_placeholder`、
  `test_sweep_keeps_baseline_tombstone_until_runtime_finalizes`。
- red→green：`git stash push -- backend/agent/aee/processor.py
  backend/agent/aee/reconciler.py` 后 → **4 failed, 20 passed**，失败集正是前 4 例；
  `stash pop` 后全绿。第 5 例（有界 + 真因）修复前后都绿——它钉的是**兜底告警出口**，
  不是缺陷本体，故不作 red 证据。
- 注入方式：`_FlakyIntentStore` 只对意图簿键（`*:emit_intents`）抛 `OSError`，
  其余状态照常落盘——与「占位失败但 processed 写成功」的真实触发条件一致。
- AEE 子集 `pytest backend/agent/tests/ -q -k "aee or emit or intent or pull or scan"`
  → 391 passed；全量结果见本 PR。
- **未做真机复现**：触发依赖一次 `set_state` 瞬时失败（SQLITE_BUSY / 磁盘满），
  本机只有逻辑链 + 注入用例证据。

## Revisit

- `_record_intent_placeholder` 里 `if not aee_type or not line: return` 仍是「静默不落
  意图」，processor 无从感知。当前两个字段由 processor 恒置非空；若将来出现别的
  `on_entry_intent` 实现，需把「未记录」也变成可观测（返回 bool 或直接抛错）。
- #2044 用尽重试后借道 `on_pull_failed` 上报，事件类型是 `PULL_FAILED` 而真因只在
  `error` 字段与日志里。若控制面要按原因分流统计，需要一个显式的 intent-failure
  事件——届时才动 `ReconcilerStats` 与前端类型，不在本批范围。
- baseline 行的 db_history 若被设备删除且 merge 始终没发生，其 done 墓碑会长期留在
  baseline 簿（意图簿无容量上限）。有实证后再加 TTL 或驱逐；正常路径不会留存超过 1 tick。
