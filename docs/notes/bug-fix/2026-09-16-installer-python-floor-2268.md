# 站点安装器解释器下限与 22.04 支持矩阵同源（#2268）

Status: implemented
Class: bug-fix

## Decision

**支持矩阵里的一行 = 该平台自带的 `python3` 必须能加载整条安装链**，这层等价关系此前没人
声明，现在把它变成一处常量 + 一条前置判据 + 一次 CI 真跑。

根因链（4 环，缺一环都不会漏到现场）：

1. `tools/release/build_bundle.py:92` 于 2026-09-15 把 Ubuntu 22.04 写进
   `SUPPORTED_PLATFORMS`，注释里的证据**只有 Agent 侧 compileall**——Agent 是独立脚本树，
   跟安装器不同源；
2. `deploy/lib/deploy-common.sh:83` 用 `python3 -m venv /opt/stp-tool` 建安装器环境，
   `tools/site_config/stages.py:603` 用 `/usr/bin/python3 -m venv <deploy-root>/venv` 建后端
   环境：两个 venv **都来自系统解释器**，所以平台矩阵直接决定解释器版本（22.04 = 3.10）；
3. `tools/site_config/models.py`、`manifest.py` 在顶层 `from typing import ... Self`，而
   `typing.Self` 是 3.11+ 的名字；
4. preflight 对 python3 只查「命令是否存在」，CI 五个 job 全跑 3.11——于是矛盾在**任何门禁
   上都不可见**，现场表现为 preflight 全绿、紧接着 install 崩在 import 阶段。

落地内容：

- **补能力而非撤回平台**：两处 `Self` 收进 `if TYPE_CHECKING:`（两文件都已有
  `from __future__ import annotations`，注解运行期不求值，所以运行期一行都不执行）；
- `tools/site_config/preflight.py:36` 新增 `MIN_PYTHON = (3, 10)`，注释写明它是「矩阵平台 ↔
  解释器」等价关系的**唯一声明处**；
- 新增 `_python_floor_check()`（`tools/site_config/preflight.py:244`，`version_info` 可注入以便
  覆盖边界），在 `run_preflight` 里排在 `_time_check` 与 `_tool_env_check` 之间：低于下限
  FAIL（code `tool_python_version`，`tools/site_config/checks.py:99` 给可执行 Fix），达标
  PASS（`tool_python_version_ok`）。这是把「下一阶段的 traceback」前移成「本阶段的判据」；
- `.github/workflows/ci.yml` 在 `pr-agent-tests` 的 checkout 后插入 setup-python 3.10 +
  `Import installer closure on the floor interpreter`：`pkgutil.iter_modules` 遍历
  `tools.site_config` 的**全部子模块**逐个 import（只 import 入口模块只能覆盖 preflight 一条
  路径，本次崩的是六个子命令）。刻意放在原 3.11 setup **之前**，`--require-hashes` 的 dev lock
  仍落 3.11，不改变既有作业语义；
- `tests/test_site_installer_python_floor.py`（新，5 用例）钉死三处一致性：AST 判别器扫安装器
  闭包、`PLATFORM_SYSTEM_PYTHON` 登记表比对矩阵、CI 步骤存在且顺序正确、preflight 双向边界；
- `docs/operations/installation.md` 前置条件与「常见 Fix 对照」各补一处；`docs/design/2026-09-multi-site-installation.md:149` 的「Python 3.11+ 环境」与 :227 的支持面行 一并改成引用 `MIN_PYTHON`——验收第 3 条要的是文档/矩阵/CI 三处同口径，只改 operations 一份会留下 设计文档反向宣称 3.11 门槛。

## Alternatives

- **撤回矩阵里的 22.04**（改文档 3 行，看似最小方案）——否。判据是能力而非惯性：全仓只有 2 处
  `typing.Self`；`python:3.10-slim` 装完 `backend/requirements.txt`（49 项）后
  `import backend.main` 成功；858 个 backend（不含已发布 `agent/scripts`）+ 55 个 tools/scripts
  文件在 3.10 下 AST 全部可解析；`tools.site_config validate --config
  deploy/sites/site.example.yaml --json` 在 3.10 与 3.13 输出逐条一致（13 checks，同 status 同
  code）。即「3.11+」不是实现约束只是无意识的漂移，撤平台等于用一个文档承诺掩盖一个未声明的约束。
- **给 release manifest 加 `min_python` 字段**——否。那是 schema/ADR 变更（跨中心与站点），本单
  要的是判据先存在；登记表暂由测试里的 `PLATFORM_SYSTEM_PYTHON` 承担。
- **改 `deploy-common.sh` 的解释器选择**（比如显式找 python3.11）——否。代码降回 3.10 后系统
  解释器已够用；另建解释器探测会把「矩阵 = 系统 python」这条关系重新藏进 shell。
- **只在 CI 加 3.10 job**——否。CI 能证明「现在跑得起来」，证明不了「操作员在 22.04 上提前知道
  跑不起来」；preflight 判据面向现场，AST/CI 判据面向防复发，三者不可互相替代。
- **给安装器全树加 `from __future__ import annotations` 就当没事**——否。它只让注解不求值，
  顶层 `import Self` 依旧执行；判别器因此**要求**TYPE_CHECKING 豁免必须与 future import 同时
  成立，缺 future import 反而报 finding。

## Verification

实跑命令（worktree `.wt/stp-2268-python-floor`；下列用例在 base `1fb4f7be` 上跑，随后 rebase 到 `6464a03f` 复跑）：

- 复现（修复前，docker `python:3.10-slim` = 3.10.21）：
  `ImportError: cannot import name 'Self' from 'typing'` @ `tools/site_config/models.py:7`；
- 修复后同镜像：`installer import closure loads on Python 3.10.21`（16 个子模块 0 失败）；
- `pytest tests/test_site_installer_python_floor.py -q` → **5 passed**（0.19s）；
- `pytest tests/ -q`（repo 级全量）→ 1155 passed（base）/ **1160 passed**（rebase 后，增量为 main 新带的用例）；
- `pytest tests/ -q -k "site or preflight or installer"` → **468 passed**（既有回归未破）；
- `ruff check tools/site_config/ tools/release/` → All checks passed；
- 破坏性对照 7 项，每项都还原（`/tmp` 备份 + `shutil.copy`，不用 `git checkout`）：
  A 恢复运行期 `Self` → AST 守卫红 **且** docker floor 步骤红（EXIT=1）；
  B 塞 `from enum import StrEnum` → 同上双红（判别器对新构造有效，非硬编码本次症状）；
  C 删 ci.yml 的 floor 步骤 → `test_ci_loads_the_closure_on_the_declared_floor` 红；
  D 矩阵加未登记的 ubuntu 20.04 → `test_support_matrix_and_floor_are_one_statement` 红；
  E 把 CI 下限改成 3.9 → `没有 setup-python 3.10 的步骤` 红；
  F 把 `MIN_PYTHON` 悄悄抬到 3.11 → 3 个用例同时红（判别器 / 矩阵 / CI）；
  G 从 `run_preflight` 摘掉 floor 检查 → `preflight_fails_closed` 红；
- 边界实测：(3,9) → FAIL `tool_python_version`；(3,10)/(3,11)/(3,13) → PASS；
- `python scripts/run_gates.py check:quick` → 10 gates OK（base 与最终 head 各跑一次）。

## Revisit

- `<deploy-root>/venv` 若被站点外手段用更旧解释器创建（跳过 `stages.py:603`），本判据不覆盖——
  只有 install 阶段的 import 崩会暴露它。真要闭环需在 verify 阶段核对 venv 解释器版本，届时应先
  有 ADR 谈 manifest 是否携带 `min_python`；
- AST 表（`_POST_FLOOR_SYMBOLS`/`_POST_FLOOR_MODULES`）是**替补**：它按符号名判断，覆盖不了
  「3.11 才修好的行为差异」这类不报 ImportError 的问题，真正的证据是 CI 那次加载；表漏项时
  症状会是 CI 红而非本地红，不要反过来删 CI；
- backend 侧在 3.10 只验到 **import 层**（`import backend.main` 成功 + 全树 AST 可解析），没跑
  `backend/tests`。矩阵若要把 22.04 升级为「受支持的完整栈」，需要补后端测试在 3.10 上的作业；
- `docs/development/local-development.md:13` 的「3.10+」在本单之后才自洽，本单**未改**该文件
  （#2330 正在改它，避免撞车）；若后续下限再抬高，改这里与 `MIN_PYTHON` 必须同一提交；
- 若将来出现第二个「平台矩阵 ↔ 能力」等价关系（如 glibc、nginx 版本），登记表应升格为 release
  manifest 字段而不是再加一张测试里的字典。
