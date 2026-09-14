# 多城市站点交付需求收敛

Status: implemented
Class: architecture

## Decision

- 将用户提出的城市 A 向 B/C 推广需求记录为[多站点交付 PRD 草案](../../prd/2026-multi-site-delivery.md)，登记到 [PRD 索引](../../prd/README.md)。
- 本次只交付需求与架构决策文档，不修改部署代码、数据库、环境变量或现网服务；本 note 的 implemented 仅指文档已落盘，不表示多站点功能已实现。
- 2026-09-14 用户先确认独立站点与本地账号基线，随后明确“暂不考虑 SSO，其他方案接受”；[ADR-0041](../../adr/ADR-0041-independent-site-delivery-and-management.md)升至 v1.1，PRD 升至 v0.3：轻量导航、统一运维和只读总览均为已确认范围，按 P1/P2/P4 分期交付；SSO 当前不纳入，跨站点写操作仍不在范围。
- 基于现有 systemd/Nginx 模板、Agent 安装/升级门禁、版本识别与备份工具区分复用基础和交付缺口；站点扩展不等于单站点多实例或跨城共享调度。
- P0 仍需确认交付环境和恢复目标；特别要求复核 ADR-0035 的受信域/主机审计触发条件，不用已确认的站点隔离架构代替安全评审。
- 用户同意继续细化后，新增 [P1 安装设计](../../design/2026-09-multi-site-installation.md)，回链 PRD 并登记文档中心：候选站点配置与既有 env 映射、离线校验/远端只读/授权验收边界、S0–S7 安装顺序和 I1–I5 实施切片。配置解析与安装工具仍未实现，不替用户选择未知的 B 环境。
- 核对发现现有 `preflight_control_plane.py` 会访问服务，`audit_stage_a_env.py` 在有管理员口令时还会执行登录/logout 探测；不能当纯只读预检。首个生产管理员引导也不能由公开注册或开发初始化脚本替代，列为明确实施缺口。

## Alternatives

- 立即实现跨城共享数据库/队列或引入 Kubernetes：不能直接解决从零安装和运维交接，且在联网与可用性要求未知时增加故障耦合，不作为本草案默认方案。
- 复制城市 A 的目录、秘密和业务库：容易串站、身份冲突与持续配置漂移，不作为新站点引导方式。
- 只扩写安装命令：遗漏更新兼容、失败恢复和移交验收，不能完整覆盖用户需求。
- 将接受导航/总览误读为接受 SSO 或跨站点执行器：混淆入口便利性、集中巡检、认证与授权；当前明确排除 SSO，不顺带修改身份协议或增加跨站点写能力。
- 直接把既有 live audit 包成默认预检或把挂载元数据检查当作可写验证：会产生隐式认证副作用或伪通过；设计拆分执行边界，真实写读与认证留给授权验收。

## Verification

- 已核对代码与相邻文档；未读取生产环境文件或真实主机清单，未对真实服务执行安装、迁移、备份恢复或生产诊断。
- 2026-09-13 初稿验证：`.venv/bin/python -m pytest tests/test_prepare_env.py tests/test_pg_restore_drill.py tests/test_deploy_postgres_hardening.py tests/test_ansible_host_key_verification.py -q`：20 passed。临时文件、命令 stub 和静态模板检查，无真实数据库连接；只检验既有工具基线，不代表新功能验收。
- 2026-09-13 初稿验证：`.venv/bin/python scripts/run_gates.py check:quick` 7 项通过；三份文档相对链接目标 24 项存在（锚点未自动校验），diff 无空白错误。
- 2026-09-14：核对 `auth.py` 本地账号校验、`users.py` 管理员用户管理、`security.py` host-only Cookie 及相关测试；未运行需隔离 PG 的后端身份测试，也未验证跨站点身份行为。
- 2026-09-14 v1.0 文档验证：`.venv/bin/python -m tools.dev.check_governance_surface --self-test` 通过（14 条规则红/绿样例）；`.venv/bin/python scripts/run_gates.py check:quick` 7 项通过；五份文档相对链接目标 84 项存在（锚点未自动校验），tracked diff 与三个新文件的空白检查通过。
- 2026-09-14 v1.1 范围确认更新：再次执行 `.venv/bin/python -m tools.dev.check_governance_surface --self-test`（14 条规则红/绿样例）及 `.venv/bin/python scripts/run_gates.py check:quick`（7 项），均通过；84 项相对链接目标存在（锚点未自动校验），diff 空白检查通过；确认验收条目共 15 项，并已移除旧“管理增强待选”口径。
- PRD MS-01 至 MS-15 均为 pending；双站点安装、断网、升级、恢复、导航与只读总览需在后续隔离环境实施验证。
- P1 设计轮验证：`.venv/bin/python -m tools.verify_control_plane_templates` 通过；再次运行环境生成、恢复 stub、PostgreSQL 模板和 SSH 校验的上述四组测试，20 passed；`.venv/bin/python scripts/run_gates.py check:quick` 7 项通过。设计/PRD/Note 的 36 项相对链接目标存在（未自动校验锚点），文档中心新增链接目标存在，diff 空白检查通过；未执行 live audit、安装或数据库操作，未将候选 YAML 视为可部署配置。
- 2026-09-14 局部手工安装基线演练（隔离单机：完成控制面部署与 1 台 Agent 心跳上线；真实分享写入、受控管理员引导、版本身份一致性、设备发现与受控主链均未验收）：暴露 12 项人工干预/失败点，作为 P1 实施切片验收样例——apt 源缺失与公网源慢；代码托管克隆停滞需改内网传输并手工修复分支；Agent resources 不入库需单独同步；数据库角色与密码需手工创建并写入 env；env 后编辑 6+ 处；首管理员仅能走开发脚本；Host 需先经 API 手工创建；adb 未在清单内；Agent 安装脚本交互式且不自动启动；Agent 缺 AEE_NFS_ROOT 即崩溃；暂存目录安装导致版本标识为空。同日 P1 设计 §8 并入对应输入行：新增内网制品通道与信任根、Agent 资源与外部工具分类、运行账户与权限模型、时间与区域、设备接入计划、版本身份注入、业务定义导入范围 7 行；平台基线、依赖与访问路径、中心存储三行并入演练观测（含 apt/pip/npm 三类包源与 `STP_AEE_NFS_ROOT` 硬依赖）；设计版本升至 0.3，演练范围与来源证明表述经复核校正；PRD 引用同步升至 0.5。演练在独立主机上执行，未读取生产 env、未连接生产库、未改动 A 的运行环境。

## Revisit

- 按已确认范围继续补齐导航与只读总览设计，不重复询问是否需要；仅在用户重新提出 SSO、统一调度、零停机或跨城共享数据等新需求时复议相关边界。
- 新站点使 ADR-0035 的受信域假设失效或触发主机级审计时，按既有裁决启动对应身份实施，不将它无条件延后。
- 首站实测后冻结安装耗时、人工干预、维护窗口及 RPO/RTO，再以 B/C 独立运行和 A 无退化证据判断推广完成。
- B 的 OS/CPU、依赖网络、安全入口及存储类型确定后冻结首个安装 profile；若超出既有模板/Agent 保护语义，先补对应设计与测试，不把示例路径或空值当作已确认配置。
