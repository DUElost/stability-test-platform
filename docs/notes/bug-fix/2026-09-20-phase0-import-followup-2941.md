# phase0 套件 7 例 ImportError：符号迁移后测试未跟随（#2941）

Status: implemented
Class: bug-fix

## Decision

**现象**（合并后回归复验发现，非本会话改动引入）：`backend/tests/test_phase0_closure.py`
的 7 个用例在 main（`9487a29b`）上确定性失败——

```
ImportError: cannot import name 'OutboxDrainThread' from 'backend.agent.main'
  test_phase0_closure.py:86 / 188 / 554 / 562 / 569 / 577（共 6 处）
```

**根因**：`261959f7 refactor(agent): extract job_runtime from main (#736)`（PR #2859）把
`OutboxDrainThread` 抽到 `backend/agent/outbox_drainer.py`（新家由
`job_runtime.py:19` 导入），测试里 6 处旧导入未同步。`backend/agent/main.py` 现在顶部
只有 stdlib 导入，**已不是 barrel**——所以正解是让测试跟随符号新家，而不是给旧路径
补兼容层。

**修法**：6 处 `from backend.agent.main import OutboxDrainThread` →
`from backend.agent.outbox_drainer import OutboxDrainThread`。仅此一处改动，无生产代码变更。

## Alternatives

- **在 `main.py` 恢复 re-export（barrel 兼容层）**：与 #736 的抽取方向相反，且全仓**没有
  第二个消费者**（`grep` 确认只有本文件的 6 处），为测试保留一个生产入口属于反向依赖；
  将来若真要恢复 barrel 语义，应作为独立决策统一做。
- **把该文件迁到 `backend/agent/tests/`**：它测的是「outbox 409 / SIGTERM 收尾 /
  current_status 解析」——既碰 agent 线程又碰控制面语义（套件头部自述覆盖
  scheduler/heartbeat/recycler/agent_api），归属需要论证；本单只修导入，迁移另议（Revisit）。
- **只改 assert 相关的一处**：7 个失败来自 6 处导入，少改一处仍红；已全量替换。

## Verification

- **main 上复现**（本机全量 `backend/tests/`，`9487a29b`）：**7 failed, 3411 passed**，
  失败面全部落在这一个文件的 6 处导入；`git log -S OutboxDrainThread -- backend/agent/main.py`
  定位到 `261959f7`，`grep` 确认全仓仅此文件仍从 `main` 取该符号。
- **修后**：`pytest backend/tests/test_phase0_closure.py -q` → **16 passed**（原 7 红 9 绿）。
- `ruff check backend/tests/test_phase0_closure.py` → All checks passed。
- 全量 `backend/tests/` 复跑（后台）与 `check:quick` 结果见 PR。
- 同类面（不是本单）：合并后回归复验同时跑了 agent 全量 **2117 passed**、
  受影响控制面套件 **212 passed**、CI 并发回归列表原样 **17 passed**、前端
  `npm run type-check` 与 `components/network` 测试 **27 passed**——均绿。

## Revisit

- **检出口缺口（本质）**：PR 阶段 `pr-agent-tests` 只跑 `backend/agent/tests/`，
  `backend/tests/` 全量只在夜间（`main-ci-backstop`，UTC 18:00）——**跨包导入改名**这类
  在 PR 阶段就能机械发现的错误，要等最多 24 小时才红。可考虑的出口：给「`backend/tests/`
  对 `backend/agent/*` 的导入」加一条收集期静态守卫（AST：被导入符号必须在目标模块存在），
  或把 `test_phase0_closure.py` 这类横跨套件纳入 PR 路径白名单。本单不做。
- 该文件是否随模块归属迁到 `backend/agent/tests/`：迁移会影响 `pr-agent-tests` 的覆盖
  与 `phase0` marker 的语义，需论证。
- `main.py` 是否恢复 barrel：见 Alternatives，若做应统一（把 #736 抽取的全部符号一次
  性决定导出面）。
