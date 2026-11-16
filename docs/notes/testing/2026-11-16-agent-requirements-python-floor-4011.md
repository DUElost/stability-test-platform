# Agent 依赖 Python 3.10 下限 CI 门禁（#4011）

Status: implemented
Class: testing

## Decision

承接 [#4011](https://github.com/DUElost/stability-test-platform/issues/4011)（来源 [#3991](https://github.com/DUElost/stability-test-platform/issues/3991)「依赖与运行契约」第 1 条），在 `.github/workflows/ci.yml` 的 required job `pr-agent-tests` 中新增一步 `Resolve agent requirements on the floor interpreter`，并在 `tests/test_agent_requirements_python_floor.py` 钉住该步骤的存在性、解释器版本与排序契约。

背景与问题结构（#3964 → #3978 → #4011）：

1. Agent 运行时的解释器下限是 Python 3.10（`backend/agent/install_agent.sh:396` 与 `backend/services/host_updater.py:316` 均以 `sys.version_info >= (3, 10)` 判定）；
2. `pr-agent-tests` 此前已有一个 Python 3.10 步骤（`Import installer closure on the floor interpreter`，#2268），但仅安装站点安装器依赖并加载 `tools.site_config` 闭包，不解析 `backend/agent/requirements.txt`；
3. #3964 将 `pydantic-settings` 下限抬至 `>=2.16.0`（要求 Python `>=3.11`）时，六项 required check 全部通过并合入，导致 17 台运行 Python 3.10 的 Agent 主机热更新失败（#3978 已加回版本上限，但缺防止同形态复发的 CI 门禁）。

落地做法：

- **`.github/workflows/ci.yml`**：在 `pr-agent-tests` 的 `Import installer closure on the floor interpreter` 之后、`setup-python` `3.11` 之前新增 `Resolve agent requirements on the floor interpreter` 步骤：
  - 复用同一 `setup-python: "3.10"` 环境，并在执行前用内联 Python 断言 `sys.version_info[:2] == (3, 10)`；
  - 执行 `python -m pip install --dry-run --ignore-installed --disable-pip-version-check -r backend/agent/requirements.txt`；
  - 使用 `--ignore-installed` 从零解析全部直接与传递依赖，避免复用或撞上上一步站点安装器装进 3.10 环境的 `pydantic` / `pyyaml` / `psycopg[binary]`；
  - 排在 3.11 `setup-python` 之前，确保后续 `pip install --require-hashes -r backend/requirements-dev.lock` 仍落在 3.11 上，且不改动 `tests/test_site_installer_python_floor.py` 对 `steps[floor_index:floor_index + 2]` 的既有定位；
  - 不新增 required check，不改六项 required check 名称。
- **`tests/test_agent_requirements_python_floor.py`**（纯离线，8 用例）：
  - 核对 `backend/agent/install_agent.sh` 与 `backend/services/host_updater.py` 声明的下限 `(3, 10)` 与 CI 守卫一致；
  - 复用 `tests/ci_workflow_probe.py` 从可执行代码行（剥离注释）定位 `pip install ... backend/agent/requirements.txt` 步骤，断言其紧前 `setup-python` 为 `"3.10"`、紧后 `setup-python` 为 `"3.11"` 且早于 `--require-hashes`；
  - 含 5 条变异自证：删步骤、注释掉命令、将紧前 `setup-python` 改为 `"3.11"`/`"3.9"`、将步骤移到 `"3.11"` 之后、删除后续 `"3.11"` 恢复步，守卫均必红。

## Alternatives

- **新建独立的 3.10 `pr-agent-requirements` required job**：否。分支保护未列入则不阻塞合入，新增 required check 违背 #4011 判据 4 与停止条件。
- **在 `pr-agent-tests` 中另建一个 `setup-python: "3.10"` + 新建 venv 后真实 `pip install`**：否。同一 job 内已有 3.10 解释器；`--dry-run --ignore-installed` 已在目标 3.10 解释器上执行完整依赖图与 `Requires-Python` 解析，无需重复下载解包 wheel，也避免与安装器冒烟步骤共享 site-packages。若未来需要把测试推进到「3.10 环境下 import/执行行为」，再评估建独立 venv 跑冒烟。
- **仅在本地/离线用 `pip install --python-version 3.10 --only-binary=:all:` 检查**：否。`--only-binary=:all:` 会在纯源码包（sdist）处误报或受限，不如直接运行在 GitHub runner 真实 Python 3.10 解释器上准确。
- **把新测试并入 `tests/test_site_installer_python_floor.py`**：否。站点安装器下限由 `tools/site_config/preflight.MIN_PYTHON` 与 `SUPPORTED_PLATFORMS` 驱动，而 Agent 下限由 `install_agent.sh` / `host_updater.py` 声明；拆成独立测试文件避免耦合两个独立发布面。

## Verification

1. **绿（当前 `main` 依赖文件，CI `pr-agent-tests` 实跑）**：
   - Run [43788671703](https://github.com/DUElost/stability-test-platform/actions/runs/43788671703/job/133041927226)（PR #4017 commit `20d4fa3d4`，Python `3.10.22`）：
   - `Resolve agent requirements on the floor interpreter` → `success`（`Would install annotated-types-0.8.0 ... pydantic-settings-2.15.0 ... xlrd-2.0.2 xlwt-1.3.0`）；`pr-agent-tests` 整 job → `success`。
2. **红（临时提交把 `pydantic-settings` 改为 `>=2.16.0,<3.0`，CI `pr-agent-tests` 实跑后撤回）**：
   - Run [43789828739](https://github.com/DUElost/stability-test-platform/actions/runs/43789828739/job/133045574185)（临时提交 `7e1e296a3`，已通过 `git reset --keep HEAD~1` 撤回，Python `3.10.22`）：
   - `Resolve agent requirements on the floor interpreter` → `failure`（exit 1）：
     `ERROR: Ignored the following versions that require a different python version: 2.16.0 Requires-Python >=3.11; 2.16.1 Requires-Python >=3.11; 2.16.2 Requires-Python >=3.11; 2.16.3 Requires-Python >=3.11`
     `ERROR: No matching distribution found for pydantic-settings<3.0,>=2.16.0`。
3. **红（删除 `.github/workflows/ci.yml` 新增步骤，本地离线测试）**：
   - `./scripts/project_python.sh scripts/run_pytest.py tests/test_agent_requirements_python_floor.py -q`（临时移除新增步骤后运行并还原）→ `7 failed, 1 passed in 0.18s`，`test_ci_resolves_agent_requirements_on_floor_interpreter` 抛出 `AssertionError: pr-agent-tests 缺少在可执行代码中用 pip install 解析 backend/agent/requirements.txt 的步骤`。
4. **离线测试与 `check:quick`**：
   - `./scripts/project_python.sh scripts/run_pytest.py tests/test_agent_requirements_python_floor.py tests/test_site_installer_python_floor.py tests/test_offline_subset_guard.py -q` → `20 passed in 0.56s`；
   - `./scripts/project_python.sh scripts/run_gates.py check:quick` → `[OK] check:quick (16 gates)`。

## Revisit

- 当所有生产 Agent 主机完成 Python `>=3.11` 升级且 `backend/agent/install_agent.sh`、`backend/services/host_updater.py` 同步上调最低版本检查时，须同步更新 `tests/test_agent_requirements_python_floor.py` 中的 `AGENT_PYTHON_FLOOR` 与 `ci.yml` 的 `setup-python` 版本（任一侧单改都会触发守卫红灯）；
- 本门禁按 #4011 守卫语义只验证 `backend/agent/requirements.txt` 在 3.10 上「依赖可解析」，不验证运行时导入与行为（后者由 `backend/agent/tests/` 在 3.11 上覆盖）；若未来出现「可安装但在 3.10 运行期报错」的依赖回归，需评估是否在 3.10 下增加 `import` 冒烟。
