# 展锐 uniview 采集路：#2079 拉取失败不得推进签名 + #2040 接入 emit 意图簿

Status: implemented
Class: bug-fix

## Decision

两单同批——同一处状态机（`backend/agent/aee/unisoc_reconciler.py`），且互相咬合：
「签名推进」决定**要不要发**，「意图簿」决定**发的时候用哪组 keys**。

### 1. #2079（P1）：签名只在本地内容**确实对应**远端签名时才推进

`_sync_device_events_to_local` 原先对每个远端目录先乐观写入
`self._pending_signatures[name] = signature`，拉取失败时**不回退**（只有「远端无
unievent_info」分支才 pop）。后果链：发射循环看到 `prev(S1) != pending(S2)` →
拿**陈旧**本地内容上报为「新异常」，成功后记下 S2 → 该 key 的签名此后不再变化，
真实新内容**既不重拉、也不再发射**（永久静默丢失）。

改法（两处，配合才完整）：

- 拉取失败 → `self._pending_signatures.pop(name)`，并把名字记入
  `self._unconfirmed_local`（每拍在同步阶段重建、`__init__` 初始为空集）；
- 发射循环对本拍未确认的名字直接 `continue`。

**为什么还需要 `_unconfirmed_local`（只 pop 不够）**：发射循环的判定是
`prev is not UNKNOWN and (signature is None or prev == signature)` ——对「从未处理过」
或「已被 #767/#2060 裁剪」的名字，`prev` 是 UNKNOWN，pending 缺失只会让它落到
「本地残留目录补发」分支，照样会拿陈旧内容发射。显式未确认集把这条也堵上。

### 2. #2040（P2）：接入 emit 意图簿（对齐 MTK 路 #1719），先落 keys 后做效果

原顺序是「emit → 成功才记 processed → 整拍结束才落盘」；崩溃落在「已 emit、未落盘」
之间（`_emit_event` 内部先 emit 再 create_local_event，天然存在这个中间态），重启重扫
同一目录会**重新分配 seq_no**——而 #1823 的持久 seq 高水位保证新号必大于旧号，
控制面按 `(job_id, seq_no)` 的去重拦不住 → 重复 log_signal + 重复 DLE。

改法：

- `_emit_event(event_dir, signature)` 拆成 **acquire（先持久化 keys）→ deliver（幂等效果）**；
- 意图记录复用 `emit_intent.load_intents/save_intents`（键仍挂在
  `{processed_state_key}:emit_intents` 命名空间），字段：
  `signature / seq_no / envelope / detected_at / dle_event_id / dle_params`；
- **复用判据 = (目录名, 内容签名) 二者同时匹配**。签名不匹配一律弃用旧记录——
  同目录被追加新异常时必须拿新 keys，否则新异常会被 `(job_id, seq_no)` 幂等挡掉
  （那正是 #2010 的语义，不能反过来被意图簿吃掉）；
- 效果侧幂等：`enqueue(seq_no, envelope)`（outbox UNIQUE `(job_id, seq_no)`）+
  `create_local_event(event_id=<预分配 UUID>)`（#1042/#1051 按 id upsert）；
- `enqueue` 用**记录里的 envelope**（含原 `job_id`）：processed 状态键按 serial 共享，
  重放可能发生在后续 Job 的进程里，必须落回原 Job 的 outbox 行（#1719 既有约定）；
- `detected_at` 首次观测即固化进记录（设备时钟不可信，重放不得产生第二个时刻）；
- **回收**：processed 落盘成功后回收本拍记录过的 keys；名字被裁剪时在
  `_prune_processed` 内收集、锁外一并回收（手工把 `del` 放在锁外的原因是那里做
  state store I/O）。`_save_processed_state()` 改为返回 bool——落盘失败就不回收，
  下一拍继续复用同一组 keys。

**有意不做**：不搬 MTK 的 `new_intent_record`（其 `parsed/output_subdir/entry_origin`
是 processor 专用形状，unisoc 无对应物）；不加 `attempts` 上限与 `done` 字段
（`done` 不参与复用判据，重放效果本身幂等；unisoc 的重放由 tick 循环天然驱动，
失败重试沿用既有「不落签名、下一拍再来」语义，不引入丢弃）。

## Alternatives

- **#2079 只 pop、不引入 `_unconfirmed_local`**：对「已处理过的目录」够用，对
  「未处理/已裁剪」的名字会退化成「拿陈旧本地内容补发」——只堵一半，否决。
- **#2079 拉取失败时把 pending 设回 `prev`**（issue 给的另一选项）：`prev` 可能是
  `_SIGNATURE_UNKNOWN` 哨兵对象，塞进签名表后发射循环会把它当「一个签名」比较，
  语义混乱；pop + 显式未确认集更直白，否决。
- **#2040 只把 processed 落盘提前到每次 emit 之后**（issue 的兜底选项"至少…"）：
  窗口只是变窄，重复**仍然发生**（重发照样拿新 seq_no），不解决被点名的
  `(job_id, seq_no)` 去重失效，否决。
- **#2040 给 unisoc 也做 sweep 式补偿**（MTK 有 `_sweep_emit_intents`）：unisoc
  的目录在同一 tick 内就会被重扫（本地树在，签名一致 → 只有 `_processed` 缺失时
  才重发），重放路径本就存在，不需要额外 sweep 线程，否决。
- **复用判据只看目录名**：会让「同目录新异常」复用旧 keys 被平台当重复丢掉
  （与 #2010 修复直接冲突），否决。

## Verification

worktree `.wt/stp-2079-2040-unisoc`，解释器 `/home/debian13/stability-test-platform/.venv/bin/python`：

```bash
python -m pytest backend/agent/tests/test_unisoc_reconciler.py -q     # 25 passed
TESTING=1 JWT_SECRET_KEY=ci-test-secret-key \
DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/stability_test' \
TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/stability_test' \
  python -m pytest backend/agent/tests/ -q                            # 2082 passed
python scripts/run_gates.py check:quick / check:pr                    # 全绿
```

新增守卫（5 条）：

- `test_pull_failure_keeps_signature_and_retries`：拉取失败不发射、不推进签名；
  下一拍拉取成功补发**新内容**（断言 `extra["aee_ts"]` 是新 kick）。反例：HEAD 上
  该用例失败（返回 1，陈旧内容被发出）。
- `test_pull_failure_skips_emit_even_for_unprocessed_dir`：未处理目录同样不得拿
  陈旧内容发射（HEAD 上失败）。
- `test_crash_window_replay_reuses_keys_no_duplicate_signal`：真实 LocalDB +
  SignalEmitter，模拟「已 emit、未落 processed」崩溃 → 重启后 `log_signal_outbox`
  仍只有 1 行、(job_id, seq_no) 复用、DLE `event_id` 与 `link_signal_seq_no` 复用
  （HEAD 上产生 2 行，失败）。
- `test_intent_removed_when_name_pruned`：裁剪名字时回收意图记录（HEAD 上失败）。
- `test_intent_removed_after_processed_persisted`：processed 落盘后簿内不留已完成
  记录、同名目录新内容拿新 seq_no。**注**：这条在 HEAD 上也通过（HEAD 无簿），
  它锁的是新机制的卫生不变量，不是 HEAD 可判定的回归。

另：`test_same_dir_with_new_content_is_repulled_and_reemitted` 的 pull 桩原先只返回
`True` 而不拷贝内容（真机上等价于「拉取未落地」），本批把桩改为真拷贝——否则它会
把「拉取失败却照发」的旧语义固化下来。

## Revisit

- 若展锐上送链整体并入 MTK 的意图簿 sweep（#1998 跟踪的四段差异收敛），本记录的
  形状与键命名应随之归一，不要两套并存太久；
- 若真机出现「目录内容变了但签名（size+mtime）没变」的证据（如原地改写、mtime
  被保留），复用判据要从签名升级为内容摘要；
- 意图簿按 (name, signature) 复用，理论上「裁剪后同名同签名目录重现」仍会复用旧
  keys（当前靠裁剪时同步回收兜住）；若观测到该形态，改为记录创建时间 + 一代性校验。
