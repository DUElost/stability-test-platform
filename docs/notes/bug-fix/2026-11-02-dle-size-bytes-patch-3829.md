# DLE 状态补丁不再清空 size_bytes（#3829）

Status: implemented
Class: bug-fix

## Decision

两处修改：

1. **服务端（根因）**：`backend/services/agent_device_log_events.py` 更新已有行时，
   `size_bytes` 改为只在 payload 显式携带（`ev.size_bytes is not None`）时赋值，
   与同文件 `plan_run_id` 的 #1052 处理同形。REMOTE / PRUNED / UPLOAD_FAILED /
   UPLOADING / PULL_FAILED 补丁不带字节数，不再把 LOCAL 登记的值覆盖成 NULL。

2. **Agent（补齐来源）**：`backend/agent/event_uploader.py` 的 REMOTE 补丁携带
   中心侧目录的实测字节。三个 REMOTE 出口（本地已删但远端存在、远端副本已一致、
   新拷贝完成）都使用 `_dir_sha256_and_size()`；该函数把字节计数并入原有的
   `_dir_sha256()` rglob + 读文件循环，**没有新增目录遍历**。

不改 `checksum` 的无条件赋值：本单判据只要求 `size_bytes`，`checksum` 的语义
（extract 取件、PRUNED 后是否保留）不在本单范围。

不回填历史 NULL：回填是生产写入，需 Owner 单独确认，本 PR 不做。

涉及文件：

- `backend/services/agent_device_log_events.py`
- `backend/agent/event_uploader.py`
- `backend/agent/tests/test_event_uploader.py`
- `backend/tests/api/test_agent_device_log_events.py`
- 本 note

## Alternatives

- **只改服务端，不让 REMOTE 携带字节**：能修复「LOCAL 有值被清空」的主路径，但
  LOCAL 登记本身缺 `size_bytes` 的行（watcher enrichment 缺值、pull failed 等路径）
  在 REMOTE 后仍是 NULL，字节台账继续有缺口。拒绝。
- **REMOTE 用独立 `sum(p.stat().st_size for p in rglob)` 统计**：会给每次 REMOTE
  增加一遍中心目录遍历；改为与既有的哈希遍历合并，零额外遍历。拒绝前者。
- **回填历史 NULL**：生产写入，超出本单授权；且历史行缺少可靠的中心侧字节来源
  （803 个事件目录需重扫）。拒绝，留给 Owner 决策。
- **同时把 `checksum` 改成显式携带才更新**：会改变 REMOTE/PRUNED 的现有语义，
  超出判据；如需变更另开单。拒绝。

## Verification

环境：worktree `.wt/fix-dle-size-bytes-patch`（base `origin/main@4dae0c49c`），
测试统一经 `scripts/run_pytest.py`（cgroup 内存硬顶）。

- `python scripts/run_pytest.py backend/agent/tests/test_event_uploader.py -q`
  → `26 passed`
- `python scripts/run_pytest.py backend/tests/api/test_agent_device_log_events.py -q`
  → `16 passed`
- `python scripts/run_pytest.py backend/tests/services/test_agent_device_log_events.py backend/tests/services/test_device_log_event_uniview_upload_1956.py -q`
  → `7 passed`
- `python scripts/run_pytest.py tests/ -q`
  → `2429 passed, 18 skipped`
- `python scripts/run_gates.py check:quick`
  → `check:quick (16 gates)` OK

反例测试与变异验证（清 `__pycache__` 后跑 `-k size_bytes`）：

- 恢复旧实现 `row.size_bytes = ev.size_bytes`：两条新测试均 FAILED（2 failed）。
- 过度修复 `if False: row.size_bytes = ev.size_bytes`：仅「显式携带按新值更新」
  FAILED（1 failed, 1 passed）——两条测试分别锁住「不清空」与「仍可更新」两个
  方向，只有正确实现同时通过。
- Agent 侧三条 REMOTE 断言分别覆盖：新拷贝（5 字节）、远端已有副本（5 字节）、
  本地已删仅远端存在（1 字节）；旧 payload 无 `size_bytes` 键时断言直接失败。

## Revisit

- 历史 NULL 是否需要回填、以及回填口径（按中心目录重扫还是按 run 汇总），需
  Owner 单独批准后再执行。
- `checksum` 目前在 PRUNED 补丁中仍会被无条件清空；若后续把 checksum 也作为
  容量/完整性台账，应同样改为「显式携带才更新」并补对应反例测试。
- REMOTE 的 `size_bytes` 是上传时刻的中心侧逻辑字节；若 B8 的 hardlink 去重改变
  物理占用统计口径，容量报表应以实际物理占用另行核算（不在本单）。
