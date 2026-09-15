# DLE 更新分支状态归一：#2025（重放 LOCAL 意图不得把 UPLOAD_PENDING 打回 LOCAL）

Status: implemented
Class: bug-fix

## Decision

`backend/api/routes/agent_api.py` 的 DLE upsert 有三条落库路径，只有两条走了
「无 scan 门禁平台」的状态归一（#1956 的 `resolve_initial_upload_state`）：

| 路径 | 位置 | 归一前 | 归一后 |
|---|---|---|---|
| 创建（预分配 id） | `agent_api.py` `state=resolve_initial_upload_state(...)` | — | ✅ 本轮前已接入 |
| 创建（无 id 新行） | 同上（第二处） | — | ✅ 本轮前已接入 |
| **更新（同 id 已存在）** | `row.state = ev.state` | ❌ **裸赋 payload 原值** | ✅ 归一为 `target_state` |

后果（本单场景）：展锐 UNIVIEW 事件入库即被提升为 `UPLOAD_PENDING`；此后
`dle_register_outbox` 的同 id、`state=LOCAL` 意图重放（该 outbox 的载荷就是注册意图，
#1042/#1051）走到更新分支，把行按原值打回 `LOCAL`——而 `LOCAL` 在同族语义里是
「已被 scan 但未被 xls 引用 → **有意不传**」：

- Agent 的 `EventUploader._recover_states()` 只捞 `UPLOAD_PENDING` → 该事件**永不重试上送**；
- `count_pending_upload_events` 只数在途三态 → **不计入**待上送，merge 门禁看不到它；
- 结果：DLE 有行、状态停在 LOCAL、无人再推进——与 #1956 要消灭的「有行、状态不动」
  是同一外部症状，只是入口在重放路径。

**改法**：更新分支与两条创建路同口径——先 `target_state = resolve_initial_upload_state(ev.event_type, ev.state)`，
**用归一后的目标态**做迁移校验与赋值（而不是 payload 原值）。

副带修正（同一行代码的必然结果）：行处于 `UPLOADING` 时收到 `state=LOCAL` 的落后补丁，
原先归一前是 `UPLOADING -> LOCAL` 表外迁移 → 409（Agent 侧 outbox 只能重试/死信）；
现在归一为 `UPLOADING -> UPLOAD_PENDING`（表内合法）→ 行回到待上送队列，由 uploader 重试。
与创建路「LOCAL 注册意图 = 请上送」的语义一致。

**未动**：`_ALLOWED_TRANSITIONS` 表本身。表中 `UPLOAD_PENDING -> LOCAL` 这条边是否还有
其它合法写入方，本轮没有证据可判（可能是控制面显式退回的通道），故不动表、只堵重放入口。

## Alternatives

- **改状态迁移表（删 `UPLOAD_PENDING -> LOCAL` 边）**：会顺带影响控制面自身的写入路径
  （表注释写明该表来自「Agent 生命周期 + 控制面标记」两侧），在没有证据表明该边无主
  的情况下删边是过度动作，否决。
- **只在更新分支特判 `UNIVIEW + LOCAL`**：等于把归一逻辑抄第二份，与三条路共用
  `resolve_initial_upload_state` 的现状背离（该 helper 的存在理由就是单点），否决。
- **让 Agent 侧重放不带 state / 只发 CREATE**：改动落在客户端与服务端两侧的协议面上，
  且老 Agent 仍会发 LOCAL 重放——服务端仍要防御，治标不治本，否决。

## Verification

worktree `.wt/stp-2025-dle-state`（base `500d6fbf`），解释器
`/home/debian13/stability-test-platform/.venv/bin/python`：

```bash
# 新增 API 行为回归（真 PG + 真表）
TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest \
  backend/tests/api/test_agent_device_log_events.py -q          # 12 passed
# 受影响面全量
TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest backend/tests/api/ -q
                                                                # 1165 passed
python -m pytest backend/tests/services/ -q -k "device_log or uniview or dle"   # 30 passed
python scripts/run_gates.py check:quick                          # [OK] 10 gates
python scripts/run_gates.py check:pr                             # [OK] 18 gates
```

新增/升级守卫（两条，均做了反例构造）：

- `test_uniview_local_register_replay_keeps_upload_pending`（API 行为，真库）：
  建行 → 断言提升为 `UPLOAD_PENDING` → 同 id、`state=LOCAL` 重放 → 断言**仍是**
  `UPLOAD_PENDING`。把 `agent_api.py` 还原到 HEAD 后该用例失败，报
  `AssertionError: LOCAL 意图重放把行打回 LOCAL`（`assert 'LOCAL' == 'UPLOAD_PENDING'`）
  ——即本单描述的现场被精确复现。
- `test_all_ingest_sites_use_the_helper`（静态，由 `test_both_ingest_sites_use_the_helper`
  升级）：原断言 `"state=ev.state" not in src` **抓不到** `row.state = ev.state`
  （多了前缀与空格）——这正是缺陷能长期存在的原因。现在同时钉住
  `target_state = resolve_initial_upload_state(...)`、`row.state = target_state`
  与两种裸赋值形态；还原到 HEAD 后该断言失败。

## Revisit

- `_ALLOWED_TRANSITIONS` 里 `UPLOAD_PENDING -> LOCAL` 的出边仍开放：若确认它已无任何
  合法写入方，应在另单删除并补回归（本轮只堵重放入口，不动表）。
- 若展锐上送链后续按 #1998 收敛（与 MTK 四段差异归一），「无 scan 门禁」这层特判是否
  还需要保留，应与 MTK 路的语义一起重审——届时 `resolve_initial_upload_state` 的
  调用点会从三处变成一处或消失。
- 本轮未做真机端到端（Agent outbox 重放 + 控制面观察）；本机 API 行为用例已覆盖同一
  路径，真机复现需设备环境。
