# 安装/升级工件链补齐 Pipeline schema（#1247 / R14-F01）

Status: implemented
Class: bug-fix

## Decision

本质问题：`schemas/pipeline_schema.json` 是 Agent 运行时必需**工件**，但此前只有
API 热更新一条入口安装它。改代码前实测三条安装链：

- `install_agent.sh` 只 `cp -r "$SCRIPT_DIR"/*`（= `backend/agent/`），无 schema；
- Ansible `install_agent.yml` 只暂存 `backend/agent/`；`update_agent.yml` rsync 目标
  仅 `{{ agent_install_dir }}/agent/`；
- 而 `pipeline_validator.py:13` 固定按 `parent.parent / "schemas"` 解析
  （安装布局 = `$INSTALL_DIR/schemas/pipeline_schema.json`）。

结果：干净安装与 Ansible 升级后，合法 Pipeline 在校验阶段失败并上报；API 热更新
（`host_updater.py:117-121,177-180`）是唯一装 schema 的路径，不能补偿其它入口。

修复 = **三条入口统一同一份工件清单（源码 + schema + 版本标识）**：

1. `install_agent.sh` 新增 `resolve_pipeline_schema`，按两种布局解析
   （仓库同构 `<script_dir>/../schemas/`、Ansible 暂存 `<script_dir>/stp_schemas/`），
   缺失即 fail-fast；新增 `resolve_code_version`（`AGENT_CODE_VERSION` 注入 >
   脚本所在 git 仓库 HEAD）写 `agent/VERSION`，与热更新同语义。
2. 新增 `backend/agent/install_selfcheck.py`：安装链末尾以安装目录为
   cwd/PYTHONPATH 跑样例 Pipeline 完整 JSON Schema 校验，失败中止安装。
3. Ansible：install 剧本暂存 schema 到 `stp_schemas/` 并注入 `AGENT_CODE_VERSION`；
   update 剧本按 sha256 检测 schema 变更 → 同步 + 纳入 restart 判定，
   VERSION 标识同批刷新。

## Alternatives

- **只在 Ansible 侧加同步、不动 install_agent.sh**——放弃：手工安装（文档主路径、
   最小部署清单/预演 runbook）仍产残件，问题本体未解；
- **把 schema 复制进 `backend/agent/` 随源码走**——放弃：schema 是后端/Agent 共享
   单源工件，复制即双源漂移税；目标路径已是热更新既有约定；
- **Ansible 侧直接 copy schema 到目标机、绕过 install_agent.sh**——放弃：脚本改用
   候选解析后暂存树里已能自洽，且安装工件逻辑保持在脚本一处（Ansible 只负责暂存）；
   否则脚本仍会因暂存树缺 schema 而 fail-fast；
- **post-install 校验用内联 heredoc**——放弃：样例与 schema 漂移无人守；独立模块
   可被 pytest 守（样例先红于主机故障）；
- **schema 纳入 Ansible 回滚快照**——放弃：与热更新语义对齐（热更新同样不回滚
   schema）；破坏性 schema 变更需另行设计，见 Revisit；
- **在脚本里归一化 schema 路径（`realpath`）**——放弃：`install` 接受含 `..` 路径，
   归一化是无收益的额外进程。

## Verification

实际运行：

- `pytest tests/ -q` → **102 passed**（含新增 `tests/test_install_agent_artifacts.py`
  10 例：解析器三种布局/优先级/缺失、版本注入与缺省、脚本落盘路径与 fail-fast、
  自检 cwd 语义、install 剧本暂存与注入）；
- `pytest backend/agent/tests/test_install_selfcheck.py
  backend/agent/tests/test_install_agent_host_id.py
  backend/tests/core/test_pipeline_validator.py -q` → **14 passed**（含新 3 例）；
- `pytest tests/test_update_agent_playbook.py -q` → **7 passed**（+1：update 剧本
  schema/VERSION 刷新与 restart 判定）；
- `ruff check .` → All checks passed；
- `ansible-playbook --syntax-check`（install / update 两剧本，example inventory）→ 通过；
- 沙箱实证安装布局语义（不入 systemd/不改本机）：构造 `agent/ + schemas/` 后
  `python -m agent.install_selfcheck` → `INSTALL_SELFCHECK_OK` rc=0；删 schema 后 →
  `INSTALL_SELFCHECK_FAIL: ... pipeline_schema.json` rc=1；
- `check:quick` → 7 gates 全绿（ruff / eslint / tsc / knip / compileall /
  gov-surface / ai-work）。

未完成（pending）：

- 验收标准第 3 条「隔离 VM 干净安装后跑样例 Pipeline 校验」与 Ansible 真实升级验证：
  本机为生产控制面宿主，不做破坏性安装；需在隔离环境执行
  `install_agent.sh` + `update_agent.yml` 后跑 `agentctl health` 与样例 Pipeline；
- `backend/agent/tests/` 全量：13 个收集错误（`DATABASE_URL` 未设，需隔离测试库），
  本单未改 DB 路径，未运行。

## Revisit

- schema 首次出现「回滚后旧代码 + 新 schema 校验失败」的事故时，把 schema 纳入
  Ansible 回滚快照（当前有意与热更新语义一致：不回滚）；
- 再出现第三类需多入口同步的运行时工件时，抽「安装工件清单」单一 manifest，
  `resolve_pipeline_schema` 改为读 manifest；
- 文档三处（`backend/agent/DEPLOY.md`、最小部署清单、预演 runbook）已同步
  `agent/ + schemas/` 布局；#1256（R14-F10）重写最小清单时需保留 schema 步骤。
