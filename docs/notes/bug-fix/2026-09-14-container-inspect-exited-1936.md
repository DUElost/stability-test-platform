# #1936 残留 testcontainer 巡检覆盖「已停未删」形态（列举补 `-a`）

Status: implemented
Class: bug-fix

## Decision

`tools/dev/check_test_containers.py` 的列举面从 `docker ps -q` 改为
`docker ps -a -q`（`_list_containers`），并补 2 例回归测试（19 → 21）、
在 `testing.md` 写明两种残留形态。

**为什么是缺陷而非范围外**：工具交付目标即「残留巡检/清理」，而残留有**两种
形态**——①（#1482）被 kill/超时的 pytest 遗留的**仍在运行**容器；②**已停未删**
的容器（宿主/daemon 重启会把运行中的容器批量优雅停掉，`autoRemove=false` 且
ryuk 不在这条路径上，于是无人回收）。原实现只列举 running，形态②**整类不可见**
且 `--strict`/`--prune` 恒报「未发现」假绿（exit 0）——与 #1714「daemon 不可用
不得报绿」同族，但缺口在列举面而非 rc 面，属独立路径。

**判定面与删除面未改**：`is_target()`（label 权威 + `testcontainers/*` 镜像兜底，
#1655）不动——用 `-a` 取回列表后走同一判定即全部命中，误伤面不变；`--prune`
仍要求 `--min-age-minutes` 阈值与显式 `--yes`。

**实测来源（2026-09-14 本机）**：在场 9 个 Exited 的 testcontainers PG 容器
（全部 `org.testcontainers=true`），`--strict` 与 `--prune --yes` 均报「未发现」。
9 个容器 `FinishedAt` 全为同一秒 `2026-08-25T15:08:49 CST`、exit=0，对应 journal
boot 记录里 `2026-08-25 15:09:14` 那次宿主机重启；即形态②的成因是**宿主/daemon
重启**，不是 pytest 被强杀。`git log -S'"ps", "-a"'` 为空、`-S'"ps", "-q"'` 指向
`37c79aa9`（#1482 引入）→ 自交付起从未带 `-a`，非回归。

## Alternatives

- **保持 `ps -q`，只在文档声明「仅覆盖 running」** → 否决：形态②是实测最常见的
  稳态残留（9 个躺 3 周零提示），把假绿合法化等于放弃工具的核心用途。
- **改用 daemon 侧过滤 `--filter label=org.testcontainers` 取代 `-a`** → 否决：
  会丢掉「label 缺失但镜像是 `testcontainers/*`」的兜底面（#1655 保留的第二判据，
  ryuk 等由 testcontainers 生态直接起的容器靠它命中）。
- **顺带把 dangling 卷纳入清理面**（本机 196 个 ≈10.4GB）→ 否决（本单范围）：
  卷只带 `com.docker.volume.anonymous`、**无** testcontainers label，归属不可判，
  自动化清理会误删他人匿名卷；需先找到可归属信号，另议。
- **给形态②加自动回收（如 daemon 启动时清理）** → 否决（本单范围）：涉及 prod
  控制面宿主的启动路径行为，须独立裁决（见 Revisit）。

## Verification

- `python -m pytest tests/test_check_test_containers.py -q` → **21 passed**（原 19）；
- **红绿双向**：把列举改回 `ps -q` 并清 `__pycache__` 后 → **恰好 2 例红**
  （`TestExitedContainerVisibility::test_exited_container_is_listed_and_flagged` /
  `::test_exited_container_is_prunable`，输出为「未发现 testcontainer」，与实测同型）；
  恢复修复版 → **21 passed**；
- **真机 A/B（本机 docker 26.1.5）**：造一个带 label 的已停容器
  （`docker run -d --label org.testcontainers=true --name stp-1936-e2e redis:7-alpine sh -c 'exit 0'`，
  状态 `Exited (0)`）——
  - 未修复版 → 「未发现 testcontainer」，**rc=0（假绿）**；
  - 修复版 → 报告「疑似残留 1 个 / ffa0395220b4 stp-1936-e2e」，**rc=1**；
  - `--prune --yes --strict --min-age-minutes 0` → 「已清理 1 个；仍有 0 个未清理」，
    **rc=0**，复核容器总数归零；验证容器及其匿名卷均已移除（卷数回到 196）；
- **真实存量处置**：主 checkout 未修复版对在场 9 个 Exited 残留报「未发现」；
  这 9 个已用 `docker rm -f` 清理（label 复核命中、无 running 依赖、`autoRemove=false`）；
- `ruff check tools/dev/check_test_containers.py tests/test_check_test_containers.py`
  → All checks passed；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (8 gates)**
  （worktree 需软链 `frontend/node_modules`，否则 eslint 缺席红）。

## Revisit

- **卷侧残留未覆盖**：196 个 dangling 卷 ≈10.4GB（194 匿名 + 2 named compose，
  其中 `stability-test-platform_postgres_dev_data` 是本地 dev 库数据，不可删）。
  匿名卷无 testcontainers label → 归属不可判。若要自动化，须先找到可归属信号
  （如按 testcontainers 容器创建时刻做时间关联），并明确「不可判即不删」的口径。
- **形态②的产生路径未处理**：本单只让残留可见，未改变「宿主/daemon 重启把容器
  停成 Exited 且无人回收」这一事实。是否在 daemon 启动/宿主引导路径加回收动作，
  属 prod 控制面宿主的行为变更，需独立裁决。
- **ryuk 回收边界**：ryuk 只在 pytest 正常退出路径有效，daemon 重启路径不参与——
  应并入 #1482 Revisit「ryuk 未回收根因」的调查面。
