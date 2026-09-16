# 依赖与本地质量检查

本文记录依赖清单分工、锁文件更新和本地质量检查。测试命令与隔离数据库要求见
[`testing.md`](./testing.md)，CI 实际参数以 [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
为准。

## 后端依赖清单

| 文件 | 内容 | 使用方 |
|---|---|---|
| `backend/requirements.txt` | 仅运行时依赖 | 生产后端镜像 |
| `backend/requirements.lock` | 运行时依赖的精确版本与 hash | 生产镜像 `--require-hashes` 安装 |
| `backend/requirements-dev.txt` | 运行时依赖 + 测试、lint 工具 | 本地开发 |
| `backend/requirements-dev.lock` | 开发依赖的精确版本与 hash | CI |

本地追新使用：

```bash
pip install -r backend/requirements-dev.txt
```

需要与 CI 逐版本对齐时使用：

```bash
pip install --require-hashes -r backend/requirements-dev.lock
```

本地环境不一定比 CI 新；排查“本地绿、CI 红”时必须先比较实际工具版本。

修改 `requirements.txt` 或 `requirements-dev.txt` 后，必须按 lock 文件抬头命令在
Python 3.11 下重新生成对应 lock。日常重生成沿用已有 pin；只有有意刷新整个允许区间
时才使用 `--upgrade`。Dependabot PR 由 `regenerate-locks.yml` 调用
`scripts/ci/regenerate-lock.sh` 补齐 lock。

`tests/test_requirements_lock.py` 与 `tests/test_requirements_dev_lock.py` 校验 source/lock
同步；测试和 lint 依赖不得加入生产 `requirements.txt`。

## Lint 与本地门禁

- Ruff 与 ESLint 都是阻塞门禁，ESLint 使用 `--max-warnings 0`；
- Ruff 规则取向见 `ruff.toml`，实际 CI 参数见 workflow；
- 前端脚本以 `frontend/package.json` 为准；
- 本地门禁入口：`python scripts/run_gates.py check:quick|pr|full`；
- `check:quick` / `check:pr` 首项是 `schema-at-head`（#1938）：比对代码 head
  与本地配置库的 `alembic_version`（`DATABASE_URL` 取自 ambient 环境 /
  `.env.backend` / `.env`）；未配置即跳过，未对齐即红——生产工作树 pull 到
  含新迁移的 main 后漏跑迁移会被这一项拦住；
- 验证顺序：Agent tests → TypeScript check → frontend build → 必要时 backend tests。
- **本机解释器比 CI 新，语法子集不对称**：CI 各 job 统一 Python 3.11（`lint` /
  `pr-compileall` / `pr-agent-tests` / `pr-typecheck` 同版，`ruff.toml` 亦
  `target-version = "py311"`），而本机 venv 是 3.13。3.12+ 才允许的写法（PEP 701 的
  同类型引号嵌套，如 `f"{item["k"]}"`）在 3.11 直接是 `SyntaxError`——本机
  `check:quick` 的 `compileall` 全绿、推上去必红。提交前用 CI 同版自查：

  ```bash
  docker run --rm -v "$PWD":/w -w /w python:3.11-slim \
    python -m compileall -q backend tools scripts
  ```

  反向不对称也存在（个别 f-string 组合在 3.13 报错而 3.11 通过，实测见
  [2026-09-16 退役判据 note](../notes/process/2026-09-16-script-retirement-guard-and-executor.md)）；
  结论一样：**别在 f-string 里内嵌引号字典键，先取局部变量**。

## 空行污染

编辑器异常可能逐行插入空行。检查或清理：

```bash
python tools/dev/collapse-blank-pollution.py [--check] <file.py>
```

脚本只处理空行并用 AST 比对语义。CI 已阻塞检查；本地钩子需一次性启用：

```bash
git config core.hooksPath .githooks
```

事故背景见
[`2026-08-14-blank-line-pollution.md`](../notes/bug-fix/2026-08-14-blank-line-pollution.md)。
