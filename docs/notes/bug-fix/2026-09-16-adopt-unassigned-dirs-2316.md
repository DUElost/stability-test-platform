# unassigned 事件目录：A 方案形状修正 + C 方案（关联时搬移）落地（#2316 / #2262 后续）

Status: implemented
Class: bug-fix

## Decision

两件事同一批：#2262 方案 A 的**形状缺陷修正**（生产实测发现）+ #2316 方案 C 落地。

### 1. 方案 A 的形状修正（先修，因为它会把 run 的 retention 拖停）

#2262 合入的 `purge_unassigned_event_dirs` 把 `remote_path` 当成**事件目录**本身
（判据 `Path(remote_path).parent == devices/unassigned`）。生产实测（只读采数）证伪：

```text
段数: 7 | 末 3 段: unassigned/7501e6b0-…-abd5ac4fa244/2026_0812_spill_db.99.ANR
```

`remote_path` 指向的是**事件目录内的一层**（Agent 记录的 dst = `{event_id}/{basename}`，
见 `event_uploader._upload_one`）。真实形态下旧判据判「形态不符」→ 计入 `failed` →
**该 run 的 retention 每轮推迟、永久停摆**（行与目录都留着；虽不影响其它 run，但那正是
本单要消除的孤儿形态）。

修正两点：

- **定位事件目录**：`remote_path` 的父目录是 unassigned 直接子目录时为它本身，祖父目录
  是直接子目录时取其父（真实形态）；
- **形态不符改判「跳过 + 告警」，不计入 `failed`**：形态问题是数据问题（行留下、目录不删
  即可），计入 failed 会把整个 run 拖成每轮推迟。删除失败（`OSError`）才推迟。

### 2. 方案 C：关联时把事件目录搬进 run 作用域

新增 `adopt_unassigned_event_dirs(db, plan_run_id)`（`backend/services/device_log_event.py`）：

- **只搬 extractable 态**（REMOTE/ARCHIVED/PRUNED）：副本已上送完，Agent 不会再往源目录写；
  在途态搬移会与 Agent 的写入分叉，留给方案 A 兜底；
- **每次 extract 前都跑**（`dedup_extract` 在 `associate_unassigned_events_to_plan_run` 之后、
  路径列举之前调用）：行可能先关联、后才变 REMOTE（Agent 末次补丁晚到）；
- **幂等/可续**：DB commit 与 rename 无法原子 → 以盘上实况为准（目标在源不在 → 只补行更新；
  源在目标不在 → rename 后更新；两者都在 → 跳过告警；都不在 → 跳过）；rename 失败一律**不动行**
  （保持 unassigned 路径，extract 照常可读），绝不做跨设备拷贝式半搬运；
- 事件目录由**行 id** 定位（`unassigned/{event_id}`），并用「remote_path 必须落在该目录内」
  把两者绑定；搬移后 remote_path 保留内层相对路径。

**决定 1（#2316 裁决）落地**：`agent_api` 的 DLE 更新分支——行**已归属且有权威路径**时，
Agent 带来的 `devices/unassigned/…` 路径**不覆盖**它（只留 `dle_unassigned_path_ignored`
日志痕迹）；行内路径为空时仍接受。**不 400**：Agent 不知道中心侧搬移，此后每次补丁都会带
这条陈旧路径，400 会把它变成状态更新失败（Agent outbox 重试/死信，#380/#764 同族）。

**方案 A 保留**（#2316 裁决 2）：C 之后 `unassigned/` 仍会有内容——从未被关联的事件、
C 的失败/崩溃窗残留、C 之前的历史行；A 是这三类的兜底。

## Alternatives

- **A 的形态判据保留原样、只把真实形态加成第二个分支**：会继续把「形态不符」计入 failed，
  保留停摆风险；分成「数据问题（跳过）」与「删除失败（推迟）」才是对的。
- **搬移范围扩到在途态**：会与 Agent 的写入分叉（它按自己算出的 unassigned 路径写）；
  且 `remote_path` 在途时本就不可靠。
- **只在关联那一刻搬**：漏掉「先关联、后变 REMOTE」的行（Agent 末次补丁晚到）——那些行会
  永远留在 unassigned（直到被 A 清掉），C 的价值大打折扣。
- **陈旧路径一律 400**：见上，会把良性陈旧路径变成状态更新失败。
- **搬移用 `shutil.move`（跨设备时退化为拷贝+删除）**：非原子、可能半搬运；`os.rename`
  失败时保持不动更安全（extract 仍能按原路径读到）。

## Verification

worktree `.wt/stp-2316-adopt-unassigned`（base `9279ee0d`），解释器
`/home/debian13/stability-test-platform/.venv/bin/python`：

```bash
TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest \
  backend/tests/scheduler/test_retention_cleanup.py \
  backend/tests/services/test_dedup_extract.py \
  backend/tests/api/test_agent_device_log_events.py -q      # 66 passed
python scripts/run_gates.py check:quick   # [OK] 10 gates
python scripts/run_gates.py check:pr      # [OK] 19 gates
```

**反例构造**（把 4 个源码文件还原到 main 后重跑同一组）→ **6 failed**：

- 2 条 retention 用例：真实形态下 A 不清理、且形态不符会推迟 run（即上面第 1 点）；
- 3 条搬移用例：C 不存在；
- 1 条 ingest 用例：陈旧路径覆盖了权威路径。

测试口径的两处自我修正（都留在用例注释里）：

- #2262 的用例把 `remote_path` 写成**事件目录本身**——正是这个不真实的口径掩盖了上面的形状
  缺陷；现已改为真实形态（`{event_id}/{basename}`），并在 helper docstring 写明为什么必须如此；
- 「崩溃窗」用例初版只搬了内层目录（源事件目录仍在）→ 走到「两者都在」分支；改为整体搬移
  事件目录后覆盖到真正的「只补行更新」分支。

## Revisit

- **剩余窗口**：`adopt` 只在 extract 前跑。若某 run 永远不再 extract（例如终态且已归档），
  其未搬的 unassigned 目录仍靠方案 A 在行删时清掉——这是有意的兜底，不是缺陷。
- **双 NULL 行**（`plan_run_id` 与 `job_id` 皆空）当前为 0：它们既不被删除谓词命中、也不会
  被 adopt（没有 run 作用域可搬）。若将来出现，需要独立 TTL 口径（#2316 第 4 项）。
- **A 的退役条件**（#2316 裁决 2 写明）：未关联事件也被纳入某个有主作用域、或双 NULL 行
  TTL 落地后，A 才能退役。
- 搬移依赖「两种布局同构」：若 `devices/{run_id}/` 下的布局再变（例如再加一层宿主维度），
  本函数的 tail 保留逻辑要同步复核。
