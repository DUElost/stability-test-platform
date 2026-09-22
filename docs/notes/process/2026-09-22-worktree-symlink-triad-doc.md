# worktree 软链清单补第三件：`.env.test`（#3137）

Status: implemented
Class: process

## Decision

`docs/development/local-development.md` §6 的软链清单从**两件**（`frontend/node_modules` + `.venv`）补为**三件**，新增 `.env.test`，并把「缺哪件会怎样」写成表。

判据是**规范入口实际依赖什么**，不是「本机还缺什么」：`scripts/run_pytest.sh`（`testing.md` 规定的测试入口）只在 `.env.test` 存在时 source 它，`.env.test` 又被 `.gitignore` 的 `.env*` 覆盖，新建 worktree 天然没有。

**关键点是这条缺失的静默性**——缺 `node_modules` 时 `check:quick` 立即红、缺 `.venv` 时 wrapper 报「没有那个文件或目录」，两者都会当场暴露；而缺 `.env.test` 时 `check:quick` **照样全绿**，只有真正走 `run_pytest.sh` 才出现 `hint: copy .env.test.example ...`。所以「门禁过了」不能证明三件套齐了，文档必须显式写出来。

### 复漏史（为什么值得写进文档而不只是记在本机备忘）

| 日期 | 情形 |
|---|---|
| 2026-09-14 | 首次踩（`.venv` 缺失 → `run_pytest.sh` 硬编码路径报错） |
| 2026-09-15 | 复踩后归纳出三件套（含 `.env.test`）并写进本机记忆 |
| 2026-09-22 | **第三次复漏**：照 §6 逐条抄，只链了两件——记忆里有三件套也挡不住「按权威文档操作」这条主路径 |

结论：本机记忆只覆盖单一 harness 会话，文档才是所有会话/工具的共同入口；同一事实写两处时，**以更全的那处为准并回写较窄的那处**（本次即把记忆里的三件套回写进文档）。

## Alternatives

1. **改 `scripts/run_pytest.sh`：缺 `.env.test` 时硬报错** —— 拒绝。wrapper 现有的 hint 分支是正确设计（它允许用 `export TEST_DATABASE_URL` 替代 `.env.test`），把它改成硬失败会砍掉这条合法用法。问题在文档不全，不在 wrapper。
2. **只记进本机记忆，不动文档** —— 拒绝。三次复漏中两次是「按文档操作」触发的，文档不补等于问题不修。
3. **顺带把 worktree 相关配置全列一遍** —— 拒绝。清单只收规范入口直接依赖的项，避免膨胀成「worktree 配置大全」后再次与事实漂移。

## Verification

- **反证（缺第三件的表现）**：在新建 worktree 里临时摘掉 `.env.test` 后执行
  `env -u TEST_DATABASE_URL ./scripts/run_pytest.sh --collect-only -q` → 首行输出
  `hint: copy .env.test.example to .env.test or export TEST_DATABASE_URL`；恢复软链后同一命令不再出现该 hint。
- **同目录子串断言**：`pytest tests/test_postgres_compose_image_2945.py -q` 通过（该文件读取 `local-development.md` 做 `POSTGRES_IMAGE` 等子串断言，确认本次改写未破坏）。
- **门禁**：`python scripts/run_gates.py check:quick` 全绿。
- **基线说明**：全量 `--collect-only` 在本机有 11 个 collection error；在纯净主检出复现**同一批 11 个**（本机环境基线，非本次改动引入）。

## Revisit

若将来再出现「规范入口依赖、但文档未登记」的本地件，按同一判据并入 §6 清单；清单只收 `run_pytest.sh` / `check:quick` 直接依赖的项，不扩散成通用 worktree 配置清单。
