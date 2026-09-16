# #2272 unisoc_reconciler 不再把「签名不可得」落成空签名；确定性失败加连续上限

Status: implemented
Class: bug-fix

## Decision

两处改动（`backend/agent/aee/unisoc_reconciler.py`）：

1. **发射守卫补 case A**：远端**列到了**该目录、但 `unievent_info` 探测/拉取失败
   （`_pending_signatures` 被 pop）时 → 本拍**不发射、不落 `_processed`**。
   判据用 `self._last_listed is not None and key in self._last_listed`；
2. **拉取循环补齐记账**：`:473 if not info:` 路径除回退 pending 外**同时** `unconfirmed.add(name)`
   （此前只 pop 不记，与 #2079 的路径判据不对称）；
3. **连续失败上限**：新增 `MAX_DIR_ATTEMPTS`（= `emit_intent.MAX_REPLAY_ATTEMPTS`，与 MTK 侧同值）
   与 `_dir_attempts` / `_abandoned_dirs`；达限放弃该目录并计入 `signals_dropped` + error 日志。

## 缺陷确认（回源码 + **实测复现**，非仅采信 issue）

issue 的两条证据经复核**属实**（`unisoc_reconciler.py:262-282`、`:596-606`），
并进一步把成因定位到**具体行**：

| 路径 | 行为 | 是否记 `unconfirmed` |
|---|---|---|
| `:461 if not info:`（探测失败） | pop pending → `signature=None` | ❌ **否**（缺口所在） |
| `:471`（拉取失败，#2079） | pop pending → `signature=None` | ✅ 是 |

即「发射守卫的不对称」有一个**上游成因**：`unconfirmed` 只覆盖了 #2079 那条路径，
探测失败那条从未被记入，只能靠发射循环的 `signature is None` 兜底——而那个兜底
只在 `prev` 已知时生效。

### 实测复现（修复前，直接跑代码）

```
修复前 tick 返回: 1 | emitter.calls: 1
修复前 _processed: {'JE.103000004': ''}      ← 空签名被写入
```

下一拍拿到真签名 `S` 时 `"" != S` → 整目录重拉 + emit 意图簿按签名建键
（`sig_key = signature or ""`）→ **重新分配 seq_no 与 DLE id** → 平台侧同一物理事件
落成**第二条记录**（`(job_id, seq_no)` 与 DLE id 是平台仅有的去重键）——
即 #2040 想关掉的重复事实类在「签名不可得」这一拍仍敞开。

## 实施中的两次自我纠正（留痕）

### 纠正 1：第一版守卫写宽了，误伤「远端列举整体失败」通道

初版直接 `if signature is None: continue`。跑既有用例 → **6 例失败**。

诊断后发现 `signature is None` 有**两种语义不同**的来源：

- **A**：远端**列到了**该目录但探测/拉取失败 → 本地内容不代表远端状态 → **不应发射**（issue 场景）；
- **B**：本拍**远端列举整体失败**（离线/adb 抖动）→ 该目录根本不在 `_pending_signatures` 里，
  **历来会发射**（本地预置/离线补发的既有通道，`test_tick_once_emits_uniview_and_creates_dle` 即此场景）。

改判据为 `_last_listed is not None and key in _last_listed` 后 → 剩 1 例失败。

### 纠正 2：为修 A 而丢掉了 B 的幂等子条件

改判据时我把原来的 `signature is None or prev == signature` 误简化成 `prev == signature`，
导致 B 场景第二拍幂等失效（`"" != None` → 重复发射，1 例失败）。
恢复 `signature is None` 子条件后 → **25 例全绿**。

> 教训：`signature is None` 同时承担「A 的止损」与「B 的不重复发」两种职责，
> 不能整体删除，只能**在 A 的入口处新增守卫**。

### 纠正 3（流程）：我的 red 验证一度给出**假信号**

第一次 red 测试时，还原脚本改坏了语法，pytest 收集失败，我误读为「29 passed」而
以为用例没有区分度；随后单独跑代码才确认修复前确实会发射并写入空签名。
重新用「整段替换」方式还原后，**红灯 2 例、绿灯 29 例**才成立。

> 教训：**red 验证必须确认还原后的代码真能运行**（`ast.parse` + 观察失败数变化），
> 否则「全绿」可能是收集失败或未生效的假象。

## Alternatives

- **只在写 `_processed` 时改成不落空签名**（如落 `None`）→ 否决：`None` 会与
  `_SIGNATURE_UNKNOWN` 语义混淆（后者是「从未处理」哨兵对象），且 `prev == signature`
  仍会因 `None != S` 触发重拉——**根因是「签名不可得时不该发射」，不是「落什么值」**。
- **把 `_processed` 的值类型改为三态（未知/空/真签名）** → 否决：改动面大
  （状态持久化格式、裁剪逻辑、既有测试均要动），而本缺陷可在发射守卫内解决。
- **对 case B 也收紧（列举失败一律不发射）** → 否决：会**破坏本地预置与离线补发**通道
  （实测 6 例既有用例），且该通道与「陈旧内容被当新异常」的 #2079 问题不同——
  B 场景下本地内容就是唯一事实源。
- **上限达限后只记日志不放弃** → 否决：issue 明确指出「每拍重拉整目录且不收敛」，
  不放弃等于没止损；且不放弃时 `signals_dropped` 会持续增长（噪声而非信号）。
- **放弃时写 `_processed[name] = ""`**（与落签名同形）→ 否决：实测该写法会被拉取循环的
  `prev == signature` 判据穿透（`"" != S` → 仍会重拉），等于**没止损**。
  改用独立的 `_abandoned_dirs` 集合，语义明确且不被其它判据干扰。

## Verification

- `pytest backend/agent/tests/test_unisoc_reconciler.py -q` → **29 passed**（既有 25 + 新增 4）；
- **红绿双向（实测代码行为，非仅测试）**：
  - 修复前：`tick_once() == 1`、`emitter.calls == 1`、`_processed == {'JE.103000004': ''}`；
  - 修复后：`tick_once() == 0`、`emitter.calls == 0`、`_processed == {}`；
  - 测试文件层面：还原后 **2 例红灯**、修复后 **29 passed**；
- **负向对照**（`test_local_listing_failure_still_emits_preseeded_event`）：
  远端整体列举失败时**仍发射**且第二拍幂等——确认未误伤 B 通道；
- **恢复路径**（`test_signature_available_next_tick_emits_once`）：探测恢复后正常发射一次；
- **上限**（`test_repeated_probe_failure_abandons_dir_after_limit`）：达 `MAX_DIR_ATTEMPTS`
  放弃该目录且 `signals_dropped >= 1`；
- **全套 agent 回归**：`pytest backend/agent/tests/ -q` → 23 failed / **2063 passed**；
  与干净 `origin/main` 基线（23 failed / 2059 passed）**失败集合逐行 diff 完全一致**
  （那 23 例为**既有**失败，与本次改动无关；本改动净增 4 例通过）；
- `ruff check` 两文件 → All checks passed；
- `check_governance_surface.py --check` → S1–S14、S5x 全绿。

## Revisit

- **`MAX_DIR_ATTEMPTS` 的阈值**：本版取 `MAX_REPLAY_ATTEMPTS`（=5）以与 MTK 侧一致，
  但两者的「一次尝试」成本不同（MTK 是重放已持久化意图，UNISOC 是**整目录 adb pull**）。
  若现场出现「瞬时故障恰好连续 5 拍」而误放弃，应改为**指数退避**而非单纯计数。
  触发条件：真实 UNISOC run 中观察到疑似误放弃的 `unisoc_reconciler_dir_abandoned` 日志。
- **放弃状态不持久化**：`_abandoned_dirs` 只在进程内，重启后会重新尝试 5 拍
  （本版认为这是**合理**的：重启本身改变了环境假设）。若需跨重启记住，应并入
  `_processed` 的持久化格式并同步裁剪逻辑。
- **`_last_listed` 作为 A/B 判据的稳健性**：该字段为裁剪逻辑（#767）所设，本版复用它
  区分 A/B。若将来裁剪逻辑改变其语义（例如列举部分失败时也填值），本判据需重新评估。
