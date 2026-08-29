# CI 依赖可复现：新增 backend/requirements-dev.lock

Status: implemented
Class: process

## Decision

CI 各 job 的 pip 安装从 `requirements-dev.txt`（开放区间）改为
`backend/requirements-dev.lock`（hash 锁定），用 `--require-hashes` 安装。

新增/改动的文件：

| 文件 | 改动 |
|------|------|
| `backend/requirements-dev.txt` | 加 `pytest-cov>=6.0,<8.0` |
| `backend/requirements-dev.lock` | **新增**，按 py3.11 用 `uv pip compile --python-version 3.11 --generate-hashes --strip-extras` 生成 |
| `.github/workflows/ci.yml` | 4 处 `cache-dependency-path` 改指 lock；3 处 `pip install` 加 `--require-hashes`；lint job 的 ruff 改为从 lock 取精确版本 |
| `tests/test_requirements_dev_lock.py` | **新增**，dev lock 同步守卫 |
| `AGENTS.md` | 依赖清单表从三份改为四份 |

**本地与 CI 走的仍是两套依赖，这是有意保留的**：本地 `pip install -r
requirements-dev.txt` 装区间内最新版（追新），CI 装 lock 锁定的版本（可复现）。
此前 CI 也走区间，于是「上游发新版 → 无关 PR 变红」，且 CI 装的与生产镜像
（`requirements.lock`）不是同一套。

方向是安全的：本地 ruff ≥ CI ruff，本地只会比 CI 更严，不会出现「本地全绿、
推上去才红」。反向不成立。

## Alternatives

**A. 只钉住 ruff**（放弃）
成本最低，只解决「lint 随机变红」这一个症状。但 CI 与生产的运行时依赖仍
可能分叉 —— CI 绿、生产炸的场景没被覆盖，而那才是代价最大的一种。

**B. CI 装 `requirements.lock` + 单独精确安装 4 个 dev 包**（放弃）
运行时部分能对齐生产。问题是第二步 `pip install pytest==x ...` 不带
`--require-hashes`，pip 为满足约束可以回改已装的任何包，锁定在第一阶段
建立、又可能在第二阶段被打破。得到的确定性是「看起来有」，比没有更危险。

**C. 完整 dev lock**（采纳）
确定性最强，且与第二份 lock 的机制完全对称（同一套 `requirements_digest.py`
摘要 + 同步测试）。代价见下。

**这份方案的真实代价**：依赖升级时从「重新生成 1 份 lock」变成 2 份。
Dependabot 提 `requirements.txt` / `requirements-dev.txt` 变更后，必须两条
`pip-compile` 都跑，否则 nightly 全量 CI 会红在同步测试上。这是用一次性的
注意力成本换「CI 不会自己变红」——单人项目里后者更贵，因为排查时上下文
已经切走了。

### 生成工具从 pip-tools 换成 uv（同一份输入，数秒 vs 卡死）

原计划沿用 `requirements.lock` 那套 `pip-compile --generate-hashes`。实测
**跑了 30 分钟仍未完成，期间 19 分钟只累积 25 秒 CPU** —— 卡在顺序请求每个
候选版本的元数据上，不是计算慢。改用 `uv pip compile --python-version 3.11`
后同一份输入**数秒**出结果。

输出格式兼容（`--hash=sha256:` 与 `# via -r requirements-dev.txt` 标记都在），
下游的 `requirements_digest.py` 与同步测试无需改动；唯一需要适配的是抬头里
的 Python 版本标记（pip-compile 写 `with Python 3.11`，uv 写
`--python-version 3.11`），同步测试两种都认。

**未同步把 `requirements.lock` 也换成 uv 生成** —— 那份是生产镜像的输入，
换工具意味着重算全部 hash，风险与收益不匹配。两份 lock 目前生成工具不同，
这是可接受的：它们守的是不同的东西。

## Verification

- `tests/test_requirements_dev_lock.py`：
  - lock 存在且带 `--hash=sha256:`；
  - 抬头含 py3.11 标记（跨版本解析的 wheel 组合在 CI 上装不上）；
    两种生成工具的写法都接受，见上文 uv 那段；
  - 摘要与 `requirements-dev.txt` 一致（抓版本/extras 漂移，集合比对看不见）；
  - 两份 requirements 声明的包都在 lock 里，且无陈旧直接依赖；
  - dev lock 必须覆盖全部运行时依赖（钉住 `-r requirements.txt` 没被误删）；
  - `pytest` / `pytest-cov` / `testcontainers` / `ruff` 必须在 lock 里 ——
    与运行时 lock 那条「它们不得出现」方向相反，别照抄。
- 该测试落在 `backend/../tests/`，PR 阶段不跑（#552 移除 `pr-backend-test`
  之后 `tests/` 只在 nightly 全量出现），**首次执行在 nightly backstop**。
- 真机验证：`pip install --require-hashes -r backend/requirements-dev.lock`
  能装出可用环境。

## Revisit

- 若 dev lock 的维护（每次依赖升级多跑一条 compile）实际比预想烦，退回方案 A，
  并在本报告记录回归原因。
- 若 CI 与本地的依赖差异造成困惑（本地绿/CI 红，或反之），改为让本地也
  装 lock；那会牺牲本地追新，需另开 note。
- 目前两份 lock 用不同工具生成（dev 走 uv、生产走 pip-tools）。若某天需要
  统一，把 `requirements.lock` 也换 uv —— 但那会重算生产镜像的全部 hash，
  必须在预发布环境验证过再合入。
- uv 是本次为生成 lock 引入的新工具（`pip install uv`，仅本地/CI 生成用，
  不进 `requirements*.txt`）。若不想在环境里多一个工具，可改成在临时容器里
  跑一次性 uv，但那样每次重新生成都要拉镜像。
