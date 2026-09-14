# 多站点 P1/I1：Linux 发行版建模与离线配置校验

Status: implemented
Class: feature

## Decision

沿用 [ADR-0041](../../adr/ADR-0041-independent-site-delivery-and-management.md) 的独立站点与非 SSO 管理边界，落实 [P1 设计](../../design/2026-09-multi-site-installation.md)的 I1，不扩张为安装器或集中式执行服务。

- 用户已确认各节点为 Linux、同 CPU 架构，可混用 Debian 13 / Ubuntu；站点级 `platform` 声明共同架构，各安装角色的 `os` 分别声明发行版/版本。不猜测 Ubuntu 版本、CPU 标识，不复制宿主 venv 或把“同架构”当兼容性证明。
- 新增独立 `tools.site_config` 模块，只公开 `validate --config`；使用已有依赖中的 Pydantic v2 / PyYAML，不导入后端运行时，不新增 env/API、数据库表或队列。
- 输入拒绝未知字段、重复 YAML 键、别名、危险标签、模糊类型、明文凭据字段、URL/路径表达式、重复目标及本地/共享目录重叠。引用保持为逻辑名，不读取绑定值或发布包。
- CLI 文本/JSON 仅输出稳定检查 ID、角色、脱敏位置/错误类别与修复建议；未知键名、文件路径、目标清单和校验异常原文均不回显。`PASS` 仅属于配置阶段，发布/秘密/远端/安装检查仍为 `BLOCKED`。
- 首个标准拓扑要求控制面、存储及各 Agent 目标分离。挂载身份、DNS 别名、远端 symlink、真实 OS/CPU 和运行时 Cookie/CSRF 均需后续授权检查，不由配置声明担保。
- [样例](../../../deploy/sites/site.example.yaml)保留 CPU、Ubuntu 版本、网络等 `null`，故意不能通过校验；测试中明确的 Ubuntu/架构值仅为合成测试输入。

本切片独立登记为 `multi-site-config-validation`（`test-impact=direct`）；前序文档执行的 `test-impact=none` 不用于声称本次代码无测试影响。共享工作树中既有的其他 Agent 脚本变更不属于本单，不修改或复制进验证副本。

## Alternatives

- 站点只有一个 OS profile：无法表达同架构但发行版/版本不同的真实拓扑，拒绝。
- 在配置校验阶段探测 SSH、解析秘密、连接 localhost DB 或复用 live audit：可能触碰安装发起机的生产状态，拒绝；只保留显式输入的无副作用阶段。
- 输出 Pydantic/YAML 异常原文或完整配置：会泄漏错填的密码、连接串、主机清单或未知键名，改为固定错误目录与脱敏字段位置。
- 现在就适配全部 Debian/Ubuntu 版本、构建离线包或加容器编排：缺少准确版本/架构、依赖网络和存储条件；留给支持矩阵与 I2/I3，不用假默认值推进写入。

## Verification

- `.venv/bin/python -m pytest tests/test_site_config.py tests/test_prepare_env.py tests/test_pg_restore_drill.py tests/test_deploy_postgres_hardening.py tests/test_ansible_host_key_verification.py -q`：**208 passed**（I1 新用例 188，既有运维回归 20）。包括独立子进程审计：拒绝网络/子进程/文件写入及隐式 env/inventory/发布包访问，并确认没有导入后端或数据库/远程运行时模块。
- `.venv/bin/python -m ruff check tools/site_config tests/test_site_config.py`：通过；`.venv/bin/python -m tools.verify_control_plane_templates`：通过。
- `.venv/bin/python -B -m tools.site_config validate --config deploy/sites/site.example.yaml --json`：实际模块入口按预期退出 **1**，报告 16 项未填配置问题；后续检查全部 `BLOCKED`，不是配置成功或安装通过。
- `python scripts/run_gates.py check:quick`：以当前 `.venv` 解释器在隔离源副本运行，**命令通过（9 项）**；其中 **8 项实际检查通过，`schema-at-head` 因未配置数据库按规则跳过**。`compileall` 提示两个未修改文件的既有文档字符串 `SyntaxWarning`（`deployment_digest.py`、`jira_issue_parser.py`），未作为本单顺手修复。
- 门禁副本基于 `d5164402`，仅叠加本需求文件；未复制运行时 env、凭据、inventory 或其他执行未提交改动。使用 `env -i`、独立 HOME/缓存目录，只复用已安装的 Python/前端依赖。门禁的数据库探测只看副本中的缺省配置，不连接生产库；无库配置时的跳过不算 schema/迁移验证通过。
- `git diff --check` 及本需求 14 个文件的空白检查通过；186 个相对链接目标存在，未自动验证 Markdown anchors。
- **pending（功能验收）**：实际 Ubuntu 版本/CPU 标识、发布支持矩阵、远端预检、安装/迁移/管理员引导、Agent 接入、真机主链、导航及 MS-01–MS-15。本单不连接生产库、不进行迁移/部署、不创建 PR 或修改运行服务。

## Revisit

- I2 消费该模型时补发布来源/兼容清单及模板计划；Agent 摘要遵循 ADR-0040，不引入竞争算法。版本支持必须有实测证据，不能继承本工具的语法 PASS。
- I3 开始系统适配前确认 Ubuntu 版本、架构标识、逐角色 OS、离线/受控镜像模式、TLS 与存储条件；数据库/Redis 的 localhost 只能在获授权的目标上下文解释。
- 如真实需求要求角色同机、不同 CPU 架构、其他发行版或路径字符，先扩展有限契约与反例测试，不以关闭检查或自动降级绕过保护。
- `site.yaml` 是部署输入而非第二套运行时配置；I2–I5 落地时继续复用现有模板、Host ID 分配与 protected env，不更改已发布脚本版本。
