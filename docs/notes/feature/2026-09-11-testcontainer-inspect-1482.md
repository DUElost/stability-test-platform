# 残留 testcontainer 巡检与安全清理工具（#1482）

Status: implemented
Class: feature

## Decision

#1482：跑 `backend/tests` 未设 `TEST_DATABASE_URL` 时 conftest 每进程起独立
`postgres:16`（隔离正确），但被 kill/超时的 pytest 进程会**遗留容器**——
本单交付巡检/清理工具，避免只能 `docker rm -f` 盲删（误删其他会话活实例）。

**工具**（`tools/dev/check_test_containers.py`）：

- 识别目标镜像 `postgres:16` / `testcontainers|ryuk`（按镜像标记过滤，
  不误伤业务容器）；
- **默认 dry-run**：列出每个容器的年龄与名称，给出建议命令（不执行删除）；
- `--prune`：仅清理 **年龄 ≥ `--min-age-minutes`（默认 120）** 的疑似残留，
  清理后再次列举复核并报告「已清理/仍剩余」；
- `--strict`：存在疑似残留时退出码 1（供手动巡检/收尾钩子）；
- docker 不可用 → 明确报错 + 退出码 2（不静默通过）。

**首次实跑审计（2026-09-11）**：36 个残留 `postgres:16`、**0 个近期活跃**，
最老 ≈ 15.9 天——比 issue 初报的 13 个严重，且说明泄漏长期存在
（ryuk 未能回收的原因待查，见 Revisit）。

**文档**：`docs/development/testing.md` 新增「测试容器与残留巡检」小节——
默认容器行为、`DATABASE_URL` 会被 conftest 覆盖（想固定库必须设
`TEST_DATABASE_URL`，受 #1300 命名护栏）、巡检/清理命令。

**清理动作未执行**：36 个残留跨多个会话（含其他 Harness 的可能活实例），
`--prune` 的手动执行时机交由维护者（工具默认阈值已保证安全），本单只交付
工具与文档。

## Alternatives

- pytest 收尾钩子自动清理：跨会话边界不可判定（无法区分「我的容器」与
  「别的会话正在用」），自动删除有误伤风险——先交付显式诊断工具；
- 直接修 ryuk 回收：根因未证（进程被 SIGKILL 时 ryuk 应仍能回收——需要
  更多观测），先有巡检工具再定位；
- 用 `docker container prune`（删所有停止容器）：范围过大，可能删非测试
  容器，且对 running 的残留无效。

## Verification

- `pytest tests/test_check_test_containers.py`：12 passed——纳秒时间戳解析 /
  空值拒绝 / 目标镜像过滤（含反例 postgres:15、stp-backend）/
  阈值边界（≥120 为残留）/ dry-run 不执行删除且给建议命令 / `--strict`
  退出码 / `--prune` 只删残留不动近期 / 空列表干净通过 / docker 不可用
  退出码 2；
- **实跑 dry-run（只读）**：识别出 36 个残留（最老 22831 分钟）、0 活跃，
  输出格式与建议命令符合预期；
- `ruff check backend/ tools/ scripts/ tests/` 全绿。

## Revisit

- ryuk 未回收根因（SIGKILL 场景 / ryuk 容器自身生命周期）待查——若定位为
  pytest 被强杀，可评估在 `conftest` 注册 `atexit`/信号处理兜底；
- 未来若把 `--strict` 接入收尾流程，需先确认「活跃判定」足够准（当前仅按
  年龄阈值；如需更准可查容器内连接数）；
- 与 #1473 无关（那是 #789 的锁测试遗留，本单是排查过程中发现的独立问题）。
