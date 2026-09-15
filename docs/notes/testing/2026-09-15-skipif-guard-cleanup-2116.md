# 删除四处锁序回归的「非 PG 就 skip」死守卫

Status: implemented
Class: testing

## Decision

把四条锁序/死锁回归里的

```python
pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="……需要 PostgreSQL 行锁……",
)
```

**删除**（连同随之无用的 `import os`；`test_abort_lock_order_1985.py` 的 `import pytest`
也一并删——它只为这条守卫而存在）。改为在文件头写明真实约束：

> 需要 PostgreSQL 行锁（FOR UPDATE / NOWAIT）。本仓 harness 总是提供 PG（conftest 的
> testcontainers / CI 的 PG service），无 PG 时在 conftest 阶段就报错——**刻意不写**
> 「非 PG 就 skip」的分支。

涉及文件：`test_shared_row_lock_order_1980.py`、`test_abort_lock_order_1985.py`、
`test_reconciler_renew_lock_order.py`、`test_retention_lock_order_2022.py`。

### 为什么删（而不是留着当防御）

1. **它不可达**：`backend/tests/conftest.py` 在导入测试模块**之前**就把 `DATABASE_URL`
   覆盖为解析出的测试库（testcontainers 或 CI 的 PG service）；`db_url_guard` 还会拒绝
   非 PostgreSQL 的显式 `TEST_DATABASE_URL`。实测：以 `DATABASE_URL=sqlite:///…` 运行，
   用例照样真跑并通过（`#2022` 的更正即基于此）。
2. **留着它有害**：一旦有一天 harness 真能解析出 sqlite（例如未来引入「快速 sqlite 子集」），
   这些 **PG 专属**回归会**静默跳过**而不是响亮失败——正是本会话一路在追的「绿而空」。
   本仓在这点上已有先例（`docs/notes/feature/2026-08-30-silent-skip-metrics.md`：静默跳过
   需要被度量），但更好的形态是**这里根本没有 skip**：非 PG harness 不是受支持的配置。

## Alternatives

- **保留守卫作为防御性写法**：否决。见上：不可达 + 把环境问题变成静默跳过。
- **换成「方言检查」型守卫**（跳过才是真的非 PG）：否决。本仓 harness 解析出的测试库
  **永远是 PG**，所以它同样是死分支，只是多几行代码；而当它真为真时，我们想要的也是**报错**
  而不是跳过。
- **用 marker（如 `-m "not needs_pg"`）替代 env 嗅探**：本单不做——目前没有任何非 PG 运行面
  需要它。若将来引入 sqlite 子集，正确做法就是 marker + 显式选择，而不是 import 期读 env。
- **给跳过加计数器**（`silent-skip-metrics` 的做法）：否决（对本处而言）。跳过在这里没有任何
  合法场景，计数只会把「不该发生的事」变成「被统计的事」。
- **顺手统一四个文件的头部措辞到一字不差**：否决。四处的 `reason` 原本就按各自用途措辞
  （回收器那条含死锁检测），统一会丢掉信息。

## Verification

| 项 | 结果 |
|---|---|
| 四个文件在**删守卫前后都**通过 | **7 passed**——即守卫本就是死码（它若为真，删掉必然改变运行） |
| `ruff check`（四个文件） | 全绿（修掉 `test_abort_lock_order_1985.py` 里随之变成未用的 `import pytest`） |
| 根 `tests/`（含锁序接线守卫 `test_lock_order_pr_path_contract.py`） | 通过（接线与本次改动无关，仍被守卫） |
| `check_governance_surface.py --check` | `[OK]` |

配套文档同步：共享行加锁表与 `#2022` Note 的 Revisit 均已标注「已收口（`#2116`）」，
保留原句并就地标注，避免读者仍按旧措辞理解。

## Revisit

- **若引入非 PG 运行面**（例如为提速做 sqlite 子集）：这四类用例必须按 **marker** 显式排除
  （`-m "not needs_pg"`），而不是回到 import 期嗅探 env——理由是 marker 会在收集期显式表达
  「我要求 PG」，而 env 嗅探会把「环境坏了」伪装成「按设计跳过」。
- **同形态守卫的其它落点**：本仓其它需要 PG 的测试若也用 env 嗅探做跳过，值得按同一判据复查
  （判据：该分支在当前 harness 下是否可达？不可达即死码；可达则是否会掩盖环境故障）。
- **`test_offline_subset_guard.py` 是不同范畴**：它守的是**根 `tests/`** 在 PR 路径上的
  「纯离线」纯度（用 `--ignore` 名单），与本节讨论的 `backend/tests/` PG 专属用例不是一回事。
