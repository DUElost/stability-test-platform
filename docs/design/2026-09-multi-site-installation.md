# 多站点 P1：站点配置、部署预检与城市 B 安装闭环

- **状态**：实现完成、现场验收待执行；I1 配置模型/离线 `validate`、I2 发布清单检查/脱敏 `plan`、I3 本地安装（S0–S4、受控管理员引导）、I4 站点侧 Agent 接入（S5、`verify` 的降级 S6）、I5 站点导航/交接证据（`/site/`、`handover`）与 I5.5 一站式部署（`deploy/*.sh`、`preflight`、`init`、inventory）均已实现；城市 B/C 现场验收（真机、存储、scan/upload/merge、签字）仍待执行，安装操作步骤见 [`installation.md`](../operations/installation.md)
- **版本**：0.9
- **v0.9 增量（2026-09-15）**：安装链 fail-open 判据收口（#2084 / #2088 / #2020 / #2017 残口）——
  新增判据见 §5 末「安装链 fail-closed 判据」，决策与放弃的备选见
  [`bug-fix/2026-09-15-site-install-fail-open-batch`](../notes/bug-fix/2026-09-15-site-install-fail-open-batch.md)
- **版本记录**：v0.8（2026-09-15）I5.5 落地——三个薄封装入口（`deploy/preflight.sh` / `install.sh` / `agent/install.sh`）、只读 `preflight`、探测驱动 `init`（≤4 问）、本地 `build_bundle`（R2 之前的发布物来源）、Ansible 式 inventory → `agents`（共享凭据 + 逐台覆盖）与「先控制面后 Agent」（`agents` 可空、`install --agents-inventory`）；`storage.provisioning` 新增 `local_mount`（本机磁盘子树，无远端身份），角色 target 隔离改为只约束「有远端管理面的角色」；本文新增 §4.2 部署契约。v0.7（2026-09-15）I5 落地——站点导航（nginx `/site/` 只读静态段 + S2 渲染，只发布获准信息）、`handover` 子命令（把 MS-01/02/04/05/06/10/13 映射到本站安装/verify 证据并落盘 `handover.json`）、安装记录新增 `runs` 计数（重跑证据）、`verify` 新增 `verify.s6.navigation`；v0.6（2026-09-15）I4 落地——控制面驱动安装链修复（`STP_AGENT_INSTALL_API_URL` 注入、安装脚本非交互、AEE 两键落盘）、站点侧 S5 编排（`agents.py`，`install --through-agents`）与 `verify` 降级 S6（登录/CSRF、Host/设备断言、noop 受控链；存储写读与 scan/upload/merge 显式 BLOCKED）；v0.5（2026-09-14）I3 落地——本地模式 `install`（S0–S4、绑定存储、安装记录/幂等/断点、`--dry-run`）与 `backend/scripts/bootstrap_admin.py` 受控首管理员引导；v0.4（2026-09-14）I2 落地——发布清单消费契约（`release.manifest`）、脱敏 `plan`（兼容/来源检查 fail-closed、`--save-dir` 保护）与 HTTPS 域名/证书占位符；v0.3（2026-09-14）§8 并入隔离演练输入基线（3 行演练观测 + 7 项新增输入，12 项人工干预清单见 Agent Note；演练范围与来源证明表述经复核校正）；v0.2（2026-09-14）I1 配置模型与离线 `validate` 落地；v0.1（2026-09-14）初稿
- **日期**：2026-09-14
- **需求**：[`多站点交付 PRD`](../prd/2026-multi-site-delivery.md) v0.9
- **架构边界**：[`ADR-0041`](../adr/ADR-0041-independent-site-delivery-and-management.md) v1.1（Accepted）

## 1. 本次细化的边界

P1 的交付对象是：在城市 B 的受支持空白 OS 上，配置独立控制面、中心存储、Agent Host，完成一条受控专项并提供站点导航。
这里的“空白”指平台尚未安装，不表示允许格式化磁盘、清空已有数据库或覆盖其他服务。

- 保持 Linux + systemd + Nginx 的生产部署路径，不将开发 Compose 改为生产入口。
- 沿用各站点本地账号；不建设 SSO、跨站点执行器或共享业务库。P4 只读总览不阻塞 P1。
- 站点配置是**部署输入**，渲染现有运行时配置，不新增第二套运行时配置解释器。
- 本设计不重开已归档 ADR-0010 的设备部署作业管线，不新建业务 DeploymentJob/Plan 类型。空白站点引导由运维工具完成，业务执行仍走现有 Plan → PlanRun → Job。
- 已确认各节点均为 Linux、CPU 架构一致，发行版可能为 Debian 13 或 Ubuntu；Ubuntu 版本与实际 CPU 架构标识尚未确认。同架构不代表系统包、Python/venv 或本地二进制可直接跨发行版复制。
- 本文不冻结未知的发行版版本、网络和存储条件，也不把本机或 A 的地址、凭据、目录和数据库用作默认目标。

## 2. 已有实现与接入方式

| 已有入口 | 已核对行为 | 本设计中的使用边界 |
|----------|------------|--------------------|
| [`tools.site_config`](../../tools/site_config/) | I1：Pydantic v2 模型、安全 YAML 读取、脱敏阶段报告；I2：发布清单消费（`release.manifest`）与脱敏 `plan`；I3：本地模式 `install`（S0–S4、绑定存储、安装记录）与 `bootstrap_admin.py` 受控管理员引导；I4：`agents.py` 站点侧 S5 编排（Host 查/建 → 驱动既有安装 → 心跳/身份/摘要/端点断言）与 `verify` 降级 S6（登录/CSRF、Host/设备断言、noop 受控链、未覆盖路径 BLOCKED）；[根级测试](../../tests/test_site_config.py)不依赖业务数据库 | 只验证显式配置与本地清单声明；`plan` 只写入显式选择的自有目录；`install` 只在声明的目标机上按阶段写入，不接收远端凭据、不建库、不格式化存储、无 `--force`；`--through-agents`/`verify` 只经本站公开 API 驱动既有执行链，不直接改 Agent `.env`、不二次实现安装器 |
| [`verify_control_plane_templates.py`](../../tools/verify_control_plane_templates.py) | 只检查仓库模板、部署根/站点占位符、Nginx/API/SocketIO 等静态不变量 | 复用为离线检查，不复制同一套断言 |
| [`prepare_env.py`](../../tools/prepare_env.py) | 首次创建 env，已有文件保持原样；新文件 0600、独占创建 | 复用创建语义；已有配置变更另做差异确认，不能假定重复调用会更新 env；秘密不得拼进 `--set KEY=VALUE` 的进程参数 |
| [控制面模板](../../deploy/control-plane/) | 已有默认服务、独立迁移 oneshot、nomigrate 常驻服务、Nginx 与 logrotate；HTTPS 模板的域名/证书路径自 I2 起为占位符 | 新安装建议组合既有迁移 oneshot + nomigrate 服务，使迁移成为显式安装步骤；不改变 A 当前服务 |
| [`preflight_control_plane.py`](../../backend/scripts/preflight_control_plane.py) | 先检查模板，再调用环境/服务探测；存在默认后端地址和 env 路径 | 不是纯离线工具；新入口必须显式传目标与环境文件，不能直接继承默认值 |
| [`audit_stage_a_env.py`](../../backend/scripts/audit_stage_a_env.py) | 访问 `/health`；有 `STP_ADMIN_PASSWORD` 时还会 POST 登录及 logout/CSRF 探测 | 认证探测可能写会话/审计等状态，只在明确授权的安装验收阶段使用；不能纳入“只读预检” |
| [`check-deploy-readiness.py`](../../tools/dev/check-deploy-readiness.py) | 解析环境并连接数据库，检查迁移和业务配置 | 保留既有用途，不作为新站点默认预检命令，也不借它试探本机生产库 |
| [Host 创建 API](../../backend/api/routes/hosts.py)与[Agent 安装服务](../../backend/services/agent_installer.py) | 控制面分配 Host ID，安装服务需要已存在的 Host；安装脚本自 I4 起非交互，回连地址由控制面 `STP_AGENT_INSTALL_API_URL` 注入（缺失/非法时 `POST /hosts/{id}/install` 返回 400 而非装出错站 Agent）；`install_options` 只下传安装目录与本地 AEE 根 | 先创建/核对本站 Host，再将响应 ID 交给安装器；不从 YAML 编造或复制 `HOST_ID`；站点侧不接收远端凭据之外的输入，也不改写 protected keys |
| [Agent 环境同步](../../backend/services/agent_env_sync.py) | 有 allowlist/protected keys；共享挂载根可统一下发，本地 AEE 根受保护 | 站内共享挂载点先采用一致约定，保留各 Host 本地磁盘差异，不直接覆盖完整 Agent env |

I3 已交付**受控首管理员引导**（`backend/scripts/bootstrap_admin.py`，由 `install` 的 S3 以目标 venv 调用）：仅首次创建（已有任何 admin 即跳过并留计数证据）、同名普通用户冲突交人工处理、不重置或提权既有账号、密码只经环境/标准输入、成功写 `initial_admin_created` 审计；公开注册与开发初始化脚本仍不得作为生产入口。

I4 已交付**站点侧 Agent 接入**（`tools/site_config/agents.py`，`install --through-agents`）与**降级 S6 验收**（`verify.py`），并修复了控制面驱动安装链的三处硬缺陷：`agent_api_url` 未下传（安装必然失败）、安装脚本只能交互读取参数（Ansible 调用会挂死）、Agent `.env` 缺少 AEE 两键（进程启动即崩）。
站点侧新增键 `STP_AGENT_INSTALL_API_URL` 由 S2 按 `public_url` 渲染并进入受管键集合：**既有站点重跑安装前必须补该键**（否则 S2 会以 `install_conflict` 阻断，而不是静默装出连错地址的 Agent）；控制面驱动安装在该键缺失/非法时返回 400，不再启动 ansible。

发布物的来源证明、数据库兼容信息及各组件版本是安装输入。Agent 内容摘要应对齐已接受的 ADR-0040，不另定义一套竞争摘要算法；具体接口以其合入实现为准，不绑定在途分支。
I2 已定义**消费端**清单契约：`release.manifest` 声明本地清单路径，`plan` 核对产品版本、组件摘要格式、来源可信声明与支持矩阵（详见 §4）；生产端（构建/打包/签名/受控渠道留痕）仍由发布侧交付，验签与真实文件哈希不在离线 `plan` 内完成。
内容摘要只能证明完整性，不能单独证明发布来源可信；写入阶段仍需确认分发/验真方案。

## 3. 站点配置契约（I1，schema_version: 1）

### 3.1 分离输入、秘密与安装记录

| 对象 | 保存什么 | 边界 |
|------|----------|------|
| `site.yaml` | 站点身份、部署角色、地址/路径、网络与安全 profile、发布物引用 | 文件由站点运维私下维护；真实目标清单不进入通用发布包或仓库样例 |
| 显式提供的秘密绑定 | 站点密钥、DB/Redis 凭据、SSH 凭据、初始管理员、证书材料等 | `_ref` 仅表示绑定名称；I3 的最小载体为 `--bindings-dir`（目录 0700、每绑定一个 0600 `KEY=VALUE` 文件，仅读取实际消费的绑定）；不强制引入 Vault/KMS；秘密来源须受限访问，不读当前 checkout 的隐式 env |
| 安装记录 | 目标站点/发布身份、脱敏配置摘要、步骤结果及恢复提示 | 保存于显式的受保护运维目录；不是业务事实库，不能凭它代替数据库/远端状态核对 |

`validate`/`plan` 不解析秘密引用。写入或已授权探测确需秘密时才读取指定绑定，不把秘密放入 argv、日志、异常原文或网页导航。
拒绝配置中的未知字段、重复键和明文秘密字段；解析错误只输出字段位置与错误类别，不回显输入值或 YAML 原文。

### 3.2 示例与平台建模

完整字段见唯一的[站点配置样例](../../deploy/sites/site.example.yaml)。样例**故意不完整，连离线 `validate` 都不能通过**：`.invalid` 是虚构目标，`null` 表示尚未确认；不能复制后直接安装。
角色的发行版分配和路径仅用于说明布局，不代表城市 B 已接受这些值。真实数据由站点管理员在本地填写，不需要在聊天或仓库提交。

Linux 家族、统一 CPU 架构和 systemd 放在站点级 `platform`；发行版与版本则逐角色声明，允许同一站点混用 Debian/Ubuntu。核心形状如下，不能作为完整配置使用：

```yaml
platform:
  os_family: linux
  cpu_arch: null
  service_manager: systemd
control_plane:
  os:
    distribution: debian
    version: "13"
agents:
  - key: agent-b-01
    os:
      distribution: ubuntu
      version: null
```

版本必须是字符串；不把 YAML 的数字 `13` 或 `24.04` 隐式转成版本。`cpu_arch` 要填已确认的 Linux `uname -m` 标识，而不是由安装发起机推断。测试中的架构及 Ubuntu 版本都是合成输入，不是支持矩阵或用户环境确认。

### 3.3 字段规则与运行时映射

| 输入 | 校验/生成规则 | 既有消费位置 |
|------|---------------|--------------|
| `schema_version` | 初版仅接受已实现的格式版本；不猜测新字段含义 | 仅安装工具，不是 pipeline schema 版本 |
| `site.id` | 稳定标识，不依赖 IP/目录/显示名；重装时必须与既有站点身份匹配 | 安装元数据及未来导航/总览；暂不预加所有业务表字段 |
| `platform` 与各角色的 `os` | `os_family=linux`、`service_manager=systemd`；架构显式统一声明，发行版仅接受 `debian` / `ubuntu` 与明确版本字符串 | I1 仅校验声明；实际 OS/CPU 核验及发布物支持矩阵留给后续阶段，不把“语法合法”报告成“已受支持” |
| `network.dependency_mode` | 仅接受 `offline` / `controlled_mirror`，无自动默认；I1 不检查或访问镜像来源 | 依赖获取计划；代理/镜像来源另行显式配置，离线模式不得回退公网 |
| `control_plane.ssh_user/ssh_credential_ref` | I5.5 起**可空**：本地模式（安装器在 `control_plane.target` 本机执行）不需要控制面 SSH；声明非空时才要求绑定存在 | 远端编排路径（未启用）；本地模式下 `init` 生成 `null` |
| `control_plane.deploy_root/deploy_user` | 专属绝对路径与服务账号；拒绝根目录、路径穿越、模板/命令注入；远端另查 symlink 和已有数据 | `<deploy-root>`、`<deploy-user>`、`STP_DEPLOY_ROOT`、systemd/logrotate 布局 |
| `control_plane.public_url` | 浏览器实际入口 origin，路径为空或 `/`，不含 userinfo/query/fragment；与安全 profile 一致 | Nginx `server_name`、`CORS_ORIGINS`、Agent `API_URL`、`STP_AGENT_INSTALL_API_URL`（I4：S2 按此渲染，是控制面驱动安装注入 Agent 的回连地址）；前端保持同源构建 `VITE_API_BASE_URL=` |
| `control_plane.tls_ref` | I1 要求 HTTPS 必须带绑定名、HTTP 不得带 TLS 绑定；证书身份/有效期与私钥权限留给后续阶段 | HTTPS 模板使用 `<server-name>`、`<tls-cert-path>`、`<tls-key-path>` 占位符（I2）；plan 只输出“待绑定”映射，不解析绑定值，绑定名不进入报告 |
| `release.bundle` / `release.manifest` | `bundle` 在 I3 按**本地目录树**消费（须含 `release-manifest.json`、`backend/`、`backend/agent/`、`backend/schemas/`、`frontend/dist-prod/`、`deploy/`、`tools/`，离线时含 `wheelhouse/`）；`manifest`（I2 新增）为本地绝对清单路径，`validate` 允许为空、`plan`/`install` 必填。清单为 ≤1 MiB 的 JSON，拒绝未知字段/重复键；要求 `product.version`（须等于 `expected_release`）、`source.revision`、至少 `agent-code` 与 `host-resources` 的 `sha256:<64hex>` 摘要、`database.schema_target`、`compatibility`（协议范围 + 逐发行版版本/架构支持矩阵）与 `provenance.attestation`（`signature` 或 `controlled_channel` 声明） | 控制面、前端 `dist-prod`、Agent、脚本/schema 及迁移的固定版本；I3 的 S0 用 ADR-0040 既有实现重算 code/resources 摘要并与清单比对（不另造算法）；签名验签与来源渠道核验仍留给发布端与后续阶段 |
| 脚本根 | 从选定发布物的既有布局派生，不另用共享盘猜测 | `STP_SCRIPT_ROOT`；不使用 `STP_NFS_ROOT/scripts` |
| `storage` | 三种 provisioning：`existing_share` 只接入分享、不接受服务器 OS/SSH 管理字段；`managed_linux` 必须声明 `os`、`ssh_user`、`ssh_credential_ref`；`local_mount`（I5.5）是**本机磁盘子树**，不写 `target/protocol/share`，也不接受任何管理字段——把本机路径写成分享会让 `site.yaml` 说谎。NFS 使用专用绝对分享路径且不带 CIFS 凭据；CIFS 使用分享名及必需的 `credential_ref` | 分享身份用于挂载；挂载点对应 `STP_AEE_NFS_ROOT`，不是控制面部署根；I1 不挂载、不格式化、不验证可达性；`local_mount` 的路径必须已是挂载点（S1 核对），`init` 负责挂盘 + bind + fstab（`nofail`） |
| `storage.mount_path` | P1 候选标准为控制面/Agent 同一字符串、同一分享；路径变体另测，不假设当前热更新可保持任意差异 | 避免现有共享根统一下发覆盖单机定制；Agent `STP_NFS_ROOT` 仅沿既有脚本别名映射 |
| `agents[].key/target` | key 仅是引导时的逻辑名；同站点避免重复名称/目标；Host ID 必须来自本站 API。I4：Host 名取 `<site.id>-<key>`，按 `target`（IP/主机名）查/建并对名称核对，占用同 IP/同名即 fail-closed（不换名、不抢占） | Host 创建参数、Ansible 目标及安装器 `agent_host_id`，Device 由 Agent 发现 |
| `agents`（列表本身） | I5.5 起**允许为空**：先把控制面装好、Agent 随后按 inventory 接入（S5 显式跳过并给出后续命令）。非空时所有 `install_root` 必须一致——`STP_SCRIPT_RUNTIME_ROOT` 是站点级单值，异构根会静默取错路径（`agent_install_root_mismatch`） | 站点级 env 渲染；S5 逐台接入 |
| `agents[].install_root/local_aee_root` | 区分安装/SSD 日志与本地 AEE 第一落点；本地 AEE 根不能误指共享挂载 | `AGENT_INSTALL_DIR` 及安装器派生路径、受保护的 `STP_AEE_LOCAL_ROOT`；I4 经 `install_options` 下传给既有安装链，空值不覆盖目标 `.env` 既有值 |
| 秘密绑定 | 按站点生成/提供，格式与权限验证，不复制 A 的值，不用占位值启动 | `DATABASE_URL`、`REDIS_URL`、`JWT_SECRET_KEY`、`AGENT_SECRET`、`SSH_CREDENTIALS_FERNET_KEY` 等既有键 |
| `navigation` | 只发布获准信息；URL 只允许受控站点/文档目标，不带凭据。I5：`contact`/`documentation_url` 由 S2 渲染进站点导航页（HTML 转义，显示名与负责人是自由文本） | `/var/www/stability-site/index.html`（0644，nginx `/site/` 只读提供）+ `handover.json`；不引入统一登录、不污染前端发布物 |

I1 的标准拓扑要求控制面、中心存储和每个 Agent 使用不同目标；不支持将多个安装角色合并到同一台机器。目标只做主机名大小写/末尾点、IP 表示法规范化；不同 DNS 别名是否指向同一机器必须在远端预检核验。
部署/Agent 安装/本地 AEE 目录不得与共享挂载根相同或互为父子，Agent 安装目录也不得包含本地 AEE 根。路径使用无转义的字母、数字、下划线、点和短横线分段；拒绝穿越、模板/命令表达式、共享根和系统目录。远端 symlink 与真实磁盘身份仍未检查。
`_ref` 只接受逻辑绑定名，不接受路径、URL、环境变量展开或明文凭据字段；DB、Redis、JWT、Agent、SSH 加密及首管理员的绑定名不能误用同一个。此检查不能证明绑定背后的值有效、互异或跨站点隔离。

安全 profile 仅沿用现有 `production` / `internal`：HTTPS 使用 secure cookie，受限 SameSite 和 CSRF 始终保留；`production + HTTP` 拒绝。
`internal + HTTP` 仅在现有 ADR-0024 豁免边界内显式选择并留痕，不能自动降级；TLS 终止位置/证书来源尚未确认时，写入阶段阻断。

首次生成配置复用 `prepare_env.py` 的受限权限及不覆盖语义；后续变更必须做脱敏差异预览和确认，不能通过删除 env 重新生成来“更新”。

I4 实验室复核后明确三条 S0/S2 语义（均已落地并测试）：
`JWT_SECRET_KEY` / `AGENT_SECRET` / `WS_TOKEN` 由安装器**首次生成**（与 `plan.generated_secret_keys` 同源），不得把模板占位值带上线；`security.ssh_encryption_key_ref` 在 S0 即被消费并校验 Fernet 键形状（留空会让密码型 Host 创建以 503 失败，站点装好却无法加主机），S2 写入 `SSH_CREDENTIALS_FERNET_KEY`；发布树落地**保持符号链接原样**（`copytree` 默认解引用会让落地树多出实体文件，ADR-0040 部署摘要随即与清单基准不一致）。

## 4. 统一入口与预检分层

日常安装只走三个入口脚本（I5.5，薄封装；操作步骤见 [`installation.md`](../operations/installation.md)）：

```bash
sudo ./deploy/preflight.sh      # 只读体检（本机）；逐项 PASS/FAIL/BLOCKED + Fix
sudo ./deploy/install.sh        # 构建发布物 → init（≤4 问）→ validate/plan → S0–S4
sudo ./deploy/agent/install.sh  # 读仓库外 inventory（默认 ~/hosts.ini）→ S5
```

三者都只调下面的 Python 入口，shell 里没有第二份校验/挂盘/建库实现；位置与默认值由
`deploy/lib/deploy-common.sh` 统一（`STP_SITE_FILE`/`STP_BINDINGS_DIR`/`STP_STATE_DIR`/
`STP_BUNDLE`/`STP_TOOL_VENV`/`STP_AGENTS_INVENTORY`）。

以下命令是这三个脚本背后的**权威接口**；在仓库根目录、已有 Python 3.11+ 环境中运行（依赖现有锁定环境内的 Pydantic v2 和 PyYAML）：

```bash
# 本机只读体检（不需要 site.yaml；零写入）
python -B -m tools.site_config preflight [--db-url <空库 DSN>] [--redis-url redis://…/1] \
  [--bundle /srv/stp-bundle] [--bindings-dir /etc/stp/bindings] [--json]
# 生成站点输入（探测可派生项，只问 ≤4 项；--fix 默认顺带准备 venv/空库/挂盘）
python -B -m tools.site_config init --output /absolute/path/site.yaml \
  --bindings-dir /etc/stp/bindings [--site-id city-b] [--no-fix] [--dry-run] [--json]
python -B -m tools.site_config validate --config /absolute/path/site.yaml
python -B -m tools.site_config validate --config /absolute/path/site.yaml --json
python -B -m tools.site_config plan --config /absolute/path/site.yaml --json
python -B -m tools.site_config plan --config /absolute/path/site.yaml --save-dir /protected/reports
# 在“已声明为目标”的控制面机内执行（本地模式；先用 --dry-run 验证）
python -B -m tools.site_config install --config /absolute/path/site.yaml \
  --bindings-dir /protected/bindings --state-dir /protected/state \
  --confirm-site <site.id> --confirm-target <control_plane.target> [--dry-run] [--json]
# 连 Agent 一起接入（S5；默认不执行，需显式开启）
python -B -m tools.site_config install … --through-agents
# inventory → agents：合并后再走同一条 S5（Host 仍由本站 API 分配；凭据落绑定目录）
python -B -m tools.site_config install … --through-agents --agents-inventory ~/hosts.ini
# 受控验收（降级 S6；同样在控制面机内执行）
python -B -m tools.site_config verify --config /absolute/path/site.yaml \
  --bindings-dir /protected/bindings [--device-serial <serial>] [--run-timeout 900] [--json]
# 交接与 P1 证据清单（读安装记录与 verify 报告，写 /site/handover.json）
python -B -m tools.site_config handover --config /absolute/path/site.yaml \
  --state-dir /protected/state [--verify-report /protected/verify.json] [--dry-run] [--json]
```

`--config` 必填，不自动发现 env/inventory。输入必须为普通 UTF-8 文件（拒绝最终路径为 symlink、目录或 FIFO），上限 1 MiB、YAML 嵌套上限 32；拒绝重复键、未知字段、非字符串映射键、多文档、危险标签和 anchors/aliases。时区使用本地公开时区库验证，不访问远端。
退出码 `0` 仅表示配置阶段通过，`1` 表示输入/配置不通过，`2` 表示 CLI 参数错误；仓库样例预期返回 `1`。
文本/JSON 报告均不回显输入值、真实目标清单、文件名或秘密；未知字段名也会脱敏。JSON 的 `checks` 与 `deferred_checks` 分开：`validate` 下发布兼容、秘密绑定、远端预检、安装验收为尚未执行的 `BLOCKED`；`plan` 下发布兼容转为实检，其余保持 `BLOCKED`——都不是安装失败或安装完成。

`plan` 在配置通过后读取 `release.manifest`（清单读取同样拒绝 symlink/目录/超大/非 UTF-8 与重复键）：清单缺失、结构不合法、缺少必需组件或来源声明、`expected_release` 与清单产品版本不一致、任一角色 OS/CPU 不在支持矩阵内，均以 `FAIL` 阻断（退出码 `1`，fail-closed）。通过后输出脱敏的模板清单（含 HTTPS 三个站点占位符与“待绑定”标记）、env 生成步骤与角色级步骤，只含字段名/数量/模板名，不含输入值、目标、路径或绑定名；来源验签与渠道授权核验保持 `BLOCKED`（不得报告为已验证）。`--save-dir` 只接受已存在、非 symlink 且属主为当前用户的目录，以 `0600` 一次性写入 `plan-report.json`，已存在时拒绝覆盖。

`install` 为**本地模式**：必须在 `control_plane.target` 声明的目标机内执行（工具环境依赖 Pydantic v2、PyYAML 与 psycopg；离线安装另需 bundle 内的 wheelhouse），S0 先核对 `--confirm-site/--confirm-target` 与配置一致、本机主机名/地址与目标相符、部署根未被其他站点占用、平台与声明一致、清单与 bundle 的 code/resources 摘要相符、以及在 `--bindings-dir`（0700，0600 `KEY=VALUE` 文件）中读取实际消费的绑定——任一不通过即 `FAIL` 且不写入。随后按 S1（目录/服务账号/依赖/挂载核对）→ S2（发布树落地、venv 与离线 wheelhouse、env 首次 0600 生成且重跑不轮换、模板渲染与站点 marker）→ S3（数据库状态分类；仅空库/本装落后执行既有迁移链，非空未接管阻断；`bootstrap_admin.py` 受控首管理员引导）→ S4（安装 nomigrate/migrate 单元与 Nginx、`nginx -t` 后 reload、`enable --now` 并轮询 `/health`）执行；迁移失败或不匹配的 schema 目标不会启动服务。安装记录写入 `--state-dir/install-state.json`（0700 目录、flock 互斥、原子写 0600），重跑逐项重核实际状态而非信任记录；`--dry-run` 只验证与规划、零写入。

`--through-agents` 追加 **S5**：按 `agents[].ssh_credential_ref` 读取 SSH 绑定（`USERNAME` + `PASSWORD` 或 `PRIVATE_KEY_PATH`；后者要求 0600 且属主为部署账号，否则 Ansible 只会以 `Permission denied (publickey)` 收场），用初始管理员对本站公开入口取 Bearer token，再按 `target` 查/建 Host（Host ID 由本站 API 分配，名称固定为 `<site.id>-<key>`）、按 `install_options` 下传安装目录与本地 AEE 根、触发既有 `POST /hosts/{id}/install` 并轮询到终态，最后断言心跳新鲜、`agent_instance_id`/`boot_id` 已记录、`agent_artifact_digest`（及非空的 `agent_resources_digest`）等于清单声明摘要、安装审计中的 `agent_api_url` 就是本站入口。响应丢失或并发重试不会重复注册：先按实际 API 状态判定，409 时重新查询并复用同一 Host/安装 run。任一 Agent 失败即停，后续 Agent 不再创建或安装。
`verify` 是**降级 S6**：登录与 CSRF 探针（无凭据、无 Origin 的写请求必须被 403 拒绝）、声明 Agent 的 Host 在线与身份断言、设备可用性、脚本目录幂等扫描（`POST /scripts/scan`，全新站点尚无目录登记），然后在授权测试设备上创建并驱动一条 `noop`（目录 `v1.0.0`、版本号 `1.0.0`）单步 Plan（Plan → 准入 → claim/租约 → 终态），要求运行终态成功、作业落在那台设备且留有 step trace（证据读 `/plan-runs/{id}/jobs`）；Watcher 需要 patrol 阶段的事件，无设备或无事件时如实 `BLOCKED`。存储写读探针与 scan/upload/merge 在本切片显式 `BLOCKED`（前者需要授权的探针子目录，后者需要真实设备日志工件），不得当作已验收。

| 操作 | 允许行为 | 禁止行为 |
|------|----------|----------|
| `validate` | 读取显式站点输入，验证结构/交叉字段，输出脱敏问题 | 解析秘密、读取实际 inventory/env、连接网络、写系统配置 |
| `plan` | 读取已声明的本地发布清单，核对版本/组件/来源声明/支持矩阵，生成脱敏步骤与模板差异；可一次性写入显式选择的自有目录 | 取远端凭据、自动下载、连接 DB/SSH、将秘密或输入值渲染到报告、覆盖既存报告 |
| `preflight` | 在**本机**执行零写入的只读探测（平台/资源/命令/端口/时钟/工具环境/声明的库与 Redis/绑定与发布物/既有站点 marker）；每条含实测事实与 Fix | 建 venv、写文件、改服务、格式化、连未声明的库、把 `BLOCKED`（未给输入）当成通过 |
| `init` | 探测可派生项并生成 `site.yaml` + 绑定目录（0700/0600）；`--fix`（默认）另建工具 venv、**空**库与角色、挂盘 + bind + fstab（`nofail`）；秘密只生成、只落文件 | 格式化磁盘、动既有数据目录、覆盖既有同名角色/库、把口令写进 `site.yaml`/报告/argv |
| `install --agents-inventory` | 解析仓库外 Ansible 式清单，合并进 `agents` 后再走既有 S5；清单凭据写入绑定目录（同 ref 不同秘密即拒绝） | 接受清单里的 Host ID（必须由本站 API 分配）、绕过站点级约束（如 `install_root` 必须一致）、在别的站点机上执行 |
| `install`（含 `--through-agents`） | 在已声明的目标机内按 S0–S4 写入已授权的新站点（本地模式）；可 `--dry-run` 只验证；S5 只经本站公开 API 创建/核对 Host 并驱动既有安装链；S2 另行渲染站点导航页到 `/var/www/stability-site/`（只发布获准信息）；安装记录与幂等/断点语义见上 | 未经确认写入、接收远端凭据、建库、格式化存储、覆盖未接管数据、`--force` 绕过保护、把安装成功宣告为站点可上线、直接改写 Agent `.env` 或 protected keys |
| `handover` | 读本站安装记录与 verify 报告，把 PRD P1 验收条目映射成证据清单并落盘 `/site/handover.json`（0644、脱敏）；`--dry-run` 零写入 | 生成含凭据/秘密名/内部地址的交接物；把缺证据条目写成通过 |
| `verify` | 对明确的新站点执行获授权的认证、受控专项与小写入探针 | 将其称为纯只读；把真实设备/业务写操作隐含在普通预检里；把 `BLOCKED`（未实现的探针/无设备）报成通过 |

所有检查须有稳定 `check_id`、目标角色、状态、脱敏说明与修复建议；结果区分 `PASS`、`FAIL`、`BLOCKED`、`NOT_APPLICABLE`。
未提供目标、缺依赖、检查没执行或证据不足均不能写为 PASS；不适用必须带理由。
进程退出成功只表示**当前阶段**的必需检查通过；输出必须标注阶段及尚未执行的检查，不允许 `validate` 的成功被解释为“可上线”。

### 4.1 检查矩阵

| 检查 | 离线阶段可证明 | 远端只读阶段可证明 | 必须留给授权写入/验收 |
|------|----------------|--------------------|----------------------|
| 配置/模板 | 类型、重复键、未知字段、路径/URL 规则、模板不变量 | 目标实际身份、既有安装/目录归属 | 首次生成文件及重复执行不改变有效秘密 |
| 发布与依赖 | 清单完整性、来源证明、支持矩阵、离线包齐全 | 实际 OS/CPU、可用工具及受控镜像连通 | 系统包安装、环境创建及运行版本核验 |
| 网络/SSH | 目标显式、无继承本机兜底 | DNS、端口、主机密钥、授权连通与超时 | 不自动接受换钥；修改网络服务另授权 |
| 存储 | 协议/分享/挂载点配置关系 | mountinfo、分享身份、容量、权限元数据 | 挂载动作、真实写入/读回及清理；只读检查不能宣称已验证写入 |
| 数据库/Redis | 目标绑定存在、版本要求明确 | 对显式新站点目标核验身份/连接/已有 schema；禁止不明确目标时连接 | 创建/迁移数据库、写入业务事实；Redis 不承担站点业务状态 |
| 账号/安全 | profile、受限 SameSite、CSRF、秘密引用完整 | 公共证书/入口、秘密文件权限等必要元数据 | 首管理员创建、登录/会话/CSRF 验收，不调用公开注册提权 |
| Agent/设备 | 安装布局、版本/schema 包齐全 | Host OS、ADB 可用性、安装是否存在；不抢占或操纵设备 | Host 创建、Agent 安装/重启、心跳/claim 与受控设备专项；I4 已把这些写入动作放到显式的 `--through-agents`/`verify` 里，不再隐含在预检中 |

远端读取必须限定为必要的安全探测，不收集完整主机清单、凭据或无关日志。
SSH 严格核对已有/获准指纹，不能用关闭主机密钥校验解决首次连接；自动重试需有次数和 deadline。
数据库/Redis 连接前必须核对已授权的目标节点与依赖身份；绑定中的 localhost/本地 socket 只在声明的目标节点上下文解释，绝不能在安装发起机上直接解析后误连本机生产服务。目标上下文不明确时阻断，不能靠一次连接成功推定目标正确。

### 4.2 部署契约（I5.5）

一次性说清「谁在什么条件下装什么、装完谁负责升级回滚」，避免把部署语义散落在脚本里。

| 项 | 契约 |
|----|------|
| 角色与执行位置 | 控制面安装器**必须在 `control_plane.target` 本机执行**（本地模式，`--confirm-target` 守卫）；中心存储默认是本机磁盘子树（`local_mount`），可换远端 NFS/CIFS；Agent 由控制面经 SSH + 本站 API 编排，不在 Agent 机器上直接改配置 |
| 支持面 | Debian 13 / Ubuntu 24.04、x86_64、systemd、Nginx、PostgreSQL、Redis；资源下限 2 核 / 4 GiB RAM / 根 ≥20 GiB。不支持 Kubernetes、开发 Compose 作生产入口、公网 `curl \| bash` |
| 发布物 | 由 `git clone` 的工作树经 `tools/release/build_bundle.py` 生成 `release-manifest.json`（ADR-0040 摘要 + alembic head + 支持矩阵 + `provenance=controlled_channel`）；R2 发布渠道就绪后只需把 `STP_BUNDLE` 指向产物（该步骤已隔离） |
| 秘密 | 站点级秘密（JWT/Agent secret/WS token/`SSH_CREDENTIALS_FERNET_KEY`/DB DSN/Redis index）由 `init` 生成并写入绑定目录（0700/0600），重跑不轮换；首管理员口令一次性生成、只在绑定文件里；任何值不进 argv、报告、日志或 `site.yaml` |
| 迁移 | 空库或「本装落后」由 S3 执行既有 alembic 链；非空且非本平台即阻断（`db_unmanaged`）；迁移失败不启动不匹配应用；清空数据库不在本工具职责内 |
| 升级/回滚归属 | 站点装完后的日常升级与回滚走**既有生产部署路径**（[`post-review-deploy-runbook`](../operations/2026-08-29-post-review-deploy-runbook.md)、热更新与 Agent 版本门禁），不由安装器承担；安装器只负责「装出可运行的第一版」，重跑用于修复与续装 |
| 离线边界 | `network.dependency_mode=offline` 时依赖与产物只来自声明介质（发布物 + `wheelhouse/`）；`preflight` 不访问公网、不下载；需要镜像/包源时由运维显式准备 |
| 幂等与断点 | 重跑逐项重核实际状态、不信任记录；同站点并发安装拒绝；`--dry-run` 零写入；失败保留产物供续装，禁止删部署根「重装」 |

## 5. 城市 B 的安装顺序与重试语义

建议将阶段结果记录到运维工作目录。记录必须绑定 `site_id`、目标身份、发布身份与脱敏配置摘要；恢复执行时重新核对实际状态，不能仅凭“上次完成”跳过。
同站点并发安装默认拒绝，至少以目标侧互斥防止两个操作者同时初始化；具体锁实现不扩展成中央调度服务。

| 阶段 | 动作与入口 | 完成条件 | 失败/重试原则 |
|------|------------|----------|----------------|
| S0 输入与目标确认 | 校验配置、发布、网络/安全 profile；明确新装模式及允许的角色目标 | 没有未填写必需项，发布可信、目标不是误指 A/本机生产 | 未通过不得进行目标写入；不能仅按 IP 不同就认定安全 |
| S1 基础与存储 | 创建专用目录/服务账号，安装声明依赖，接入或配置已授权分享（I5.5：`local_mount` 由 `init` 挂盘 + bind + fstab；S1 只核对挂载点） | 角色目录归属、分享身份、必要依赖和容量满足要求 | 不格式化数据盘；已存在状态需核对，不能覆盖或全目录清理 |
| S2 发布与环境 | 固定发布物落地，复用模板，生成独立站点秘密/配置 | 无遗留占位符、权限正确，前端路径与 Nginx root 一致 | 保留已成功创建的秘密；重复执行不轮换密钥或复制其他站点身份 |
| S3 数据库与管理员 | 对显式新站点数据库执行既有迁移 oneshot；完成受控首管理员引导 | schema 达到发布目标；管理员可用且审计留痕 | 非空/未接管数据库阻断 fresh-install；迁移失败不启动不匹配应用；已有管理员不重置、不重复创建，同名普通用户冲突需人工处理 |
| S4 控制面入口 | 启动 nomigrate 服务及 Nginx，核对 DB/Redis/SAQ、同源入口和 SocketIO | 服务健康、必要后台组件就绪，登录/CSRF 经授权验收 | 不用跳过基础设施检查伪造成功；先完成私有引导再暴露正式入口 |
| S5 Host 与 Agent | 经本站管理员权限创建/核对 Host，消费 API 返回 ID，复用 Ansible/安装服务（I4：`install --through-agents`，`tools/site_config/agents.py`；I5.5：**先装控制面再按 inventory 接 Agent**，`agents` 可空、S5 显式跳过并提示 `deploy/agent/install.sh`） | Agent 指向本站、身份唯一、代码/schema/脚本一致，心跳与设备发现正常；I4 逐项断言：心跳新鲜、实例/启动标识、摘要=清单、审计入口=本站 | 已安装且归属其他站点则拒绝；不能重复注册 Host、重写 protected keys 或静默转走现有设备；失败即停，不继续下一个 Agent |
| S6 受控主链与存储 | 选择专用测试设备，验证 Plan/claim/租约、Watcher、scan/upload/merge 及授权写读探针（I4：`verify` 的降级路径） | 结果、日志位置、文件引用及终态清理有证据；I4 具备：noop 单步 Plan 到终态 + step trace 落在指定设备 | 失败保留证据，不盲目重跑刷机/硬件动作；探针只清理本次创建的文件；无设备、存储探针与 scan/upload/merge 记 `BLOCKED`，不得报成通过 |
| S7 导航与交接 | 提供站点入口、负责人、运维文档、安装摘要和后续维护/备份计划（I5：`/site/` + `handover` + [运维文档](../operations/site-handover-and-navigation.md)） | 独立入口可用，导航无凭据，P1 对应验收签字；I5 逐项映射 MS-01/02/04/05/06/10/13 并把缺证据条目保持 BLOCKED | P1 完成不代表 P2 升级恢复或 P4 总览已交付；`handover.json` 是证据快照，不是签字 |

首管理员引导仅用于已确认的新站点初始化：复用现有密码校验/哈希与审计，禁止默认弱密码、秘密入 argv、覆盖现有密码或为已有普通用户静默提权。
实际 API 与数据库初始化顺序需补针对隔离库的测试，不以开发初始化命令能运行作为生产适用证据。

存储写读探针必须在确认分享身份后，使用明确的专用探针子目录与唯一文件名，按原样读回并仅删除本次文件。
挂载未就绪时禁止向本机同名目录落数据；只有元数据检查通过时，结果应写“待授权写入验证”，不能宣称存储端到端已通过。

**安装链 fail-closed 判据（v0.9：#2084 / #2088 / #2020 / #2017）**

- `security_profile` 与 scheme 必须一致：`internal` 是 ADR-0024 v1.1 的「无 TLS 内网」豁免位，
  声明 https 即 `internal_https_profile_conflict` 阻断。nginx/env 模板对只按 profile 选择，
  放开该组合会静默装出 `listen 80` 与 `AUTH_COOKIE_SECURE=0`，把豁免边界突破成「声明加密、实际明文」；
- S4 写共享系统路径（systemd unit / nginx site / logrotate）**前先全量判归属**：既有文件不含本站
  部署根即 `install_conflict` 且零写入。服务名与站点名全局固定，同机第二站点必然同名（PRD：
  「一个站点是一组角色，不是一台服务器」），无判据的覆盖会把别站服务改指本站部署根。本站重跑
  先在 `--state-dir/shared-path-prev/` 留旧内容副本（不往系统路径扔 `.bak`，避免被 logrotate 收走）；
  发行版 `sites-enabled/default` 只移出 `sites-enabled` 停用为 `sites-available/stp-disabled-default`，不删除；
- S0 重算摘要所用的 digest 实现取**安装器自身源码树**的受信副本，bundle 只作为被测数据传入——
  量具不得来自被测物（旧实现以 `PYTHONPATH=ctx.bundle` 起子进程 import 被校验树，能同时改写量具
  与产物即可自洽，且实际取哪一份取决于解释器的 `sys.path` 顺序）；
- S2 的 `.env.backend` 与 unit/nginx 共用同一占位符守卫：受管键与模板键失配即阻断，占位值不落盘
  （本仓文档自 v0.5 起就写着「无遗留占位符」，此前只有模板侧兑现了它）。

## 6. 拆分为可验证实施单

以下为建议拆分，不是已创建的 Issue/PR，也不是对目录的永久所有权划分。

| 顺序 | 实施切片 | 最小修改面 | 通过标准 |
|------|----------|------------|----------|
| I1（已实现） | 配置模型与离线校验 | `tools/site_config/`、`deploy/sites/site.example.yaml` 与 `tests/test_site_config.py`；Pydantic v2 | 合成输入覆盖缺失/未知字段、重复键、明文凭据字段/错误脱敏、URL/路径注入、混合发行版和无运行时副作用；不代表安装通过 |
| I2（已实现） | 发布输入与模板计划 | `tools/site_config/` 新增发布清单消费与 `plan`（`manifest.py`、`plan.py`）、HTTPS 模板域名/证书占位符、`tools/verify_control_plane_templates.py` 与两份部署文档同步、新增 `tests/test_site_config_plan.py` | 一份模板适配两组合成站点；发布兼容与来源检查失败阻断；秘密不经 argv 或报告；`--save-dir` 一次性 `0600` 落盘且不覆盖 |
| I3（已实现） | 新站点基础安装（本地模式） | `tools/site_config/` 新增 `install.py`/`bindings.py`/`ops.py`/`stages.py`、`backend/scripts/bootstrap_admin.py`、`tests/test_site_install.py`；平台/存储适配限定为 Debian 13 + x86_64 + existing_share/NFS（挂载由运维预先完成） | 在一次性容器中完成 S0–S4；重复执行、断点、错误目标、迁移失败与未接管库行为有测试；`--dry-run` 零副作用 |
| I4（已实现） | Agent 接入与闭环 | 复用 Host API、现有 Agent 安装、protected env 与升级门禁；`tools/site_config/agents.py`+`verify.py`、控制面驱动安装链修复（`STP_AGENT_INSTALL_API_URL`/非交互脚本/AEE 两键） | 双 Host 接入不串站，响应丢失后重试不重复注册；指定测试设备完成 S5–S6（无真机时用 `noop`+静态设备序列号的降级路径并标 pending，不得当已验收） |
| I5（已实现） | 导航、文档与验收 | 站点导航页模板 + nginx `/site/` 只读段 + `handover` 子命令（`tools/site_config/handover.py`）+ 运维交接文档与签字位 | S7、PRD MS-01/MS-02/MS-04/MS-05/MS-06/MS-10/MS-13 的 P1 部分有可复跑证据（真机与存储等保持 BLOCKED） |

I1/I2 已在合成配置与合成清单中验证（临时目录，无网络、无子进程审计）；I3 已在合成夹具与一次性容器中验证本地安装链（含幂等重跑、断点、错误目标与迁移失败负例）；I4 已在合成夹具（Fake API/无网络）与 238 隔离容器实验室验证站点侧编排（双 Host 接入、重复触发、错目标负例、noop 受控链）——容器实验室的 Agent 运行时依赖等价替身，真机 S6 与 scan/upload/merge 仍标 pending。验证记录见 [I1 Agent Note](../notes/feature/2026-09-14-multi-site-config-validation.md)、[I2 Agent Note](../notes/feature/2026-09-14-multi-site-release-plan.md)、[I3 Agent Note](../notes/feature/2026-09-14-multi-site-site-install.md) 与 [I4 Agent Note](../notes/feature/2026-09-14-multi-site-agent-onboarding.md)。远端编排与真实 B 现场适配（含 Ubuntu/HTTPS/CIFS）仍须先完成第 8 节输入确认。
所有新增运行时 env/API 若确有必要，分别同步环境变量权威文档及前端 API 类型入口；本次设计不预先添加这些字段。

## 7. 验证策略与完成判据

- 静态/单元：合成配置、临时目录、命令 stub；沿用 [测试边界](../development/testing.md)，不读取生产 env 或真实 inventory。
- 模板回归：现有 `verify_control_plane_templates.py` 与环境生成/SSH 校验测试继续成立；不能以新的安装器绕过旧模板不变量。
- 安装集成：一次性 VM/隔离数据库/独立端口和路径；覆盖空库、已存在但未授权接管、部分安装、重跑、掉线、错误挂载及依赖不可用。
- 双站点隔离：两份合成输入可复用同一发布物，站点密钥与业务事实独立；入口/路径/身份混用时 fail-closed。
- 站点侧编排：I4 的 S5/S6 用 Fake API 覆盖请求序列与状态机（不联网、不起 ansible），再在隔离容器实验室里以真实控制面 + 2 个 Agent 容器复核端到端链路；重复触发、错误目标、他站占用与摘要不符均为 fail-closed 负例。
- 真机验收：仅经授权的 B 试点设备，完成当前声明支持的专项链路；不把模拟测试等同于真机成功。
- P1 安装成功必须附逐阶段结果及未覆盖项；P2 的升级/恢复演练和 P4 的只读总览仍单独验收。

当前交付 I1 配置模型/离线 `validate`、I2 发布清单检查/脱敏 `plan`、I3 本地安装（S0–S4 + 受控管理员引导）与 I4 站点侧 Agent 接入（S5 + 降级 S6），验证记录见 [I1 Agent Note](../notes/feature/2026-09-14-multi-site-config-validation.md)、[I2 Agent Note](../notes/feature/2026-09-14-multi-site-release-plan.md)、[I3 Agent Note](../notes/feature/2026-09-14-multi-site-site-install.md) 与 [I4 Agent Note](../notes/feature/2026-09-14-multi-site-agent-onboarding.md)。I5 已实现站点导航（`/site/`）与交接证据（`handover`，含实验室内 `/site/` 可达与证据映射验收）；**城市 B/C 现场执行**（真实设备 S6、真实存储写读、scan/upload/merge、浏览器点击核对与签字）仍未执行；现有局部测试与单容器验收不能代替现场验收。

## 8. 实施适配前仍需确认的输入

2026-09-14 在隔离环境完成了一次局部手工安装基线演练：单机内完成控制面部署与 1 台 Agent 心跳上线。真实分享写入、受控管理员引导、版本身份一致性、设备发现与受控主链（S6）均未验收，不据此宣称任一安装阶段完成。下表的“演练观测”仅为该次观测，不构成对 B/C 的承诺；演练暴露的失败点与人工干预清单作为 §6 实施切片的验收样例，记录在 [Agent Note](../notes/architecture/2026-09-13-multi-site-delivery-requirements.md)。
I4 随后在同类的隔离容器实验室复核了站点侧编排链（双 Host 接入不串站、重复触发不双启动、错目标 fail-closed、noop 受控主链），容器 Agent 使用资源/存储替身，真机、真实分享与 scan/upload/merge 仍未验收。

| 输入 | 需要确认的最小信息 | 缺失时的处理 |
|------|--------------------|--------------|
| B 的平台基线 | 已确认 Linux、同 CPU 架构，发行版可能为 Debian 13 / Ubuntu；仍需实际架构标识、Ubuntu 版本及逐角色发行版分配；宿主解释器版本决定离线依赖目标。演练观测：Debian 13 + Python 3.13 环境完成了局部手工安装（控制面部署与 Agent 心跳），但现有依赖锁按 3.11 生成，宿主 venv 与锁/wheelhouse 须统一到同一解释器 | 不擅自选择 x86_64 或某个 Ubuntu 版本，不假定 venv/二进制跨发行版兼容；I1/I2 用合成配置继续 |
| 依赖与访问路径 | 完全离线还是可访问公司镜像；apt/pip/npm 三类包源各自的地址与回退顺序；入口 HTTP/HTTPS、TLS 终止位置、证书来源及授权 SSH 路径。演练观测：公网制品源不可靠（代码托管克隆停滞、官方系统源约 0.2MB/s），三类包源的最优选择互不相同，不能共用一条“放行公网”结论 | 不默认允许公网下载，不自动降级安全 profile，不探测未知目标 |
| 内网制品通道与信任根 | 代码与资源的分发方式（内网镜像/受控介质/受控传输）与来源可信方案（签名及信任根，或受控渠道的授权与留痕；摘要只证明完整性、不能单独证明来源）、能否携带百 MB 级 Agent 资源与离线依赖。I2 已交付消费端清单契约、I3 已交付 bundle 目录布局与 code/resources 真实摘要比对（§3.3/§4）；构建、打包与签名/渠道留痕的生产端仍待确认 | 阻断写入阶段，不生成发布物；不得以“源码压缩包”替代 |
| 中心存储（日志服务器） | 新建 Linux 分享或已有 NAS，NFS/CIFS、容量/权限与本地盘布局。演练观测：Agent 启动硬依赖 `STP_AEE_NFS_ROOT`，缺失即崩溃；本地目录替身只能用于隔离演练 | 不格式化、不创建未知分享、不假设所有挂载字符串都可原样覆盖；真实写入/读回单独授权 |
| Agent 资源与外部工具分类 | 逐项声明“随包分发/现场预置/不可离线”：Agent resources（aimonkey、flashtool）、扫描工具、adb 与 udev 规则、固件与许可 | 不承诺对应链路（刷机、扫描等）在目标站点可用 |
| 运行账户与权限模型 | 安装器以何身份运行、提权凭据如何传入（不得进入 argv、日志与报告）；部署用户与 Agent 用户命名；sudo 最小授权范围。I4 新增：若 Agent 使用私钥认证，私钥须为本部署账号所有且 0600（Ansible 以该账号读取），站点需确认密钥下发/所有权归属 | 阻断写入阶段；不得沿用既有站点的账号名假设 |
| 时间与区域 | 站点时区、NTP 源与验收阈值。演练观测：源站与目标机时区不同且初始未同步 | 预检不通过即阻断 |
| 设备接入计划 | 首站 ADB/USB 与 udev 策略、S6 验收载体（真机或明确替代方案）、设备数量级；I4 已提供降级路径（`verify` + `noop/v1.0.0` + 静态设备序列号），真机仍须单独确认 | S6 记“未覆盖”，不得以模拟测试替代真机结论；降级路径的无设备/存储/scan 检查以 `BLOCKED` 呈现 |
| 版本身份注入 | 无 git checkout 安装时，控制面与 Agent 两侧如何提供产品版本与来源 revision。演练观测：按现有手工流从暂存目录安装时 Agent 版本标识为空 | 视为未完成安装，不得报成功 |
| 首站规模与维护 | Host/Device 数量级、长跑作业避让、维护窗口、恢复目标及首站验收负责人 | 不承诺固定安装分钟数或零停机；先记录基线与可测目标 |
| 业务定义导入范围 | 哪些 Plan/Tool/Project 需随站点复用及承载方式（当前仅套件具备 XML 工件级导入导出） | 默认空站点；不复制源站业务库 |

只需确认上述类型与约束，不需要提供密码、连接串、私钥或完整主机清单。具体部署目标和秘密在实施时由站点管理员通过受保护输入提供。
