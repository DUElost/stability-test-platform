# 多站点 P1/I4：站点侧 Agent 接入（S5）与降级 S6 验收

Status: implemented
Class: feature

## Decision

沿用 [ADR-0041](../../adr/ADR-0041-independent-site-delivery-and-management.md) 的独立站点边界，落实 [P1 设计](../../design/2026-09-multi-site-installation.md) 的 I4（§3.3/§4/§5）：站点工具只经**本站公开 API** 驱动既有 Host/安装/执行链，不另立第二套安装器，也不直接改写 Agent `.env` 或 protected keys。

- **控制面驱动安装链修复**：`STP_AGENT_INSTALL_API_URL`（S2 按 `public_url` 渲染，进受管键集合）由 `POST /hosts/{id}/install` 注入 `-e agent_api_url`，缺失/非法即 **400** 而不启动 ansible；`install_agent.sh` 非交互化（env 优先、仅 TTY 才提示、缺 `AGENT_API_URL` 退出 1）；`.env` 模板补齐 `STP_AEE_NFS_ROOT`/`STP_AEE_LOCAL_ROOT`（空值不写、不覆盖既有非空值）；`install_options` 允许 `agent_install_root`/`agent_local_aee_root`/`agent_nfs_root` 三个可选键并随安装审计留痕。
- **S5 编排**（`tools/site_config/agents.py`，`install --through-agents`）：读取每个 `agents[].ssh_credential_ref`（`USERNAME` + `PASSWORD` 或 `PRIVATE_KEY_PATH`，后者要求 0600 且属主为部署账号）→ 初始管理员取 Bearer token → 按 `target` **查/建** Host（名称 `<site.id>-<key>`，Host ID 由本站 API 分配）→ 触发安装并轮询 → 断言心跳新鲜、`agent_instance_id`/`boot_id`、`agent_artifact_digest`（及非空 resources 摘要）等于清单声明、安装审计 `agent_api_url` = 本站入口、设备发现（无设备记 `BLOCKED`）。
- **幂等/失败语义**：响应丢失或并发重试走同一路径（先按实际 API 状态判定，409 时重查并复用同一 Host/安装 run）；他站占用同名/同 IP、退役主机、缺失或形状错误的绑定一律 fail-closed；任一 Agent 失败即停，后续 Agent 不再触碰。
- **降级 S6**（`tools/site_config/verify.py`）：登录 + CSRF 探针（无凭据无 Origin 的写必须 403）、Host/设备断言、脚本目录幂等扫描后在授权设备上驱动 `noop` 单步 Plan（Plan → 准入 → claim/租约 → 终态 + step trace）；Watcher 事件、存储写读探针、scan/upload/merge 显式 `BLOCKED`。
- **I4 实验室暴露并修复的既有缺陷**（都在“从零安装”路径上）：
  1. `SSH_CREDENTIALS_FERNET_KEY` 从未被安装链消费 → 站点装好后**密码型 Host 创建 503**；现由 `security.ssh_encryption_key_ref` 绑定在 S0 提供并校验 Fernet 形状。
  2. `JWT_SECRET_KEY`/`AGENT_SECRET`/`WS_TOKEN` 只写模板占位值（`plan` 声称“生成”）；现由 S2 首次生成、重跑不轮换。
  3. install playbook 在安装脚本创建服务账号**之前**就 `chown android` → 全新主机第一个任务即失败；现在暂存目录不设 owner。
  4. 全新安装不写 `ARTIFACT_DIGEST(_RESOURCES)` → 站点侧只能看到空摘要；现按与热更新相同基准在控制机计算并落盘。
  5. `regex_search('...(\\S+)', '\\1')` 返回**列表**，`install_agent.yml` 会把 `["sha256:…"]` 写进 `ARTIFACT_DIGEST`（Agent 校验失败 → 空摘要）；安装路径补 `| first`。`update_agent.yml` 的同类缺陷由 issue #2112 跟踪、在途 PR #2120 修复——本切片不重复改动该文件（开工前已复核重叠窗口），只保留安装路径。
  6. S2 用 `copytree` 落地发布树时**解引用符号链接**，落地树多出实体文件 → ADR-0040 部署摘要与清单基准不一致；改为保链接落地（重跑先清冲突项）。
  7. `HostOut` 不暴露 `agent_artifact_digest`/`agent_resources_digest` → 外部只能读库才能比对内容身份；现随 `HostOut` 与前端类型一并暴露。
  8. `verify` 侧三处平台契约适配：PlanStep 必须显式 `timeout_seconds`（否则 422 `INVALID_LIFECYCLE`）、脚本版本号是目录名去掉 `v` 的 `1.0.0`、Job 证据只在 `/plan-runs/{id}/jobs`（详情内嵌 jobs 不带 step_traces）。

## Alternatives

- **站点侧另写一套安装/执行**：与“复用既有执行链”冲突，会引入第二套状态与凭据面；改为只做 API 编排 + 断言。
- **只支持私钥 SSH**：可绕开 Fernet 绑定问题，但把现场最常见的密码路径留在坏的 503 上；改为两条都支持并修绑定消费。
- **摘要缺失时放行**：会让“内容一致性”变成口号；保持 fail-closed（`agent_digest_missing`/`run_evidence_missing`），并把等待窗做成有界（摘要在下一次心跳才上报、trace 落库晚于状态翻转）。
- **verify 自行造设备/跳过扫描**：会让 S6 变成模拟验收；改为真设备可用才跑链，脚本目录扫描（幂等）后建计划，其余显式 `BLOCKED`。
- **把 `JWT_SECRET_KEY` 等留成模板占位值**（现状）：等于交付共享弱秘密的站点；改为首次生成、重跑不轮换（与 `generated_secret_keys` 声明对齐）。

## Verification

- **仓库离线**（worktree，base=origin/main `dbec59e1`，全部改动未提交前已跑）：`pytest tests/ -q` → **662 passed**（含新增 `tests/test_site_agents.py` 52、`tests/test_install_agent_noninteractive.py` 16；I3 `tests/test_site_install.py` 16 含新负例）；`pytest backend/tests/api/test_hosts.py` → 46 passed（testcontainers，含 `TestHostInstallEndpoint` 与新的摘要暴露用例）；`backend/tests/services/test_agent_installer.py` → 15 passed；`pytest backend/tests/api/test_heartbeat*.py` → 51 passed；`ruff check backend/ tools/ scripts/` 通过；`scripts/run_gates.py check:quick` **10 gates OK**；`env_inventory.py --check` 一致（213 个读取名，新增 `STP_AGENT_INSTALL_API_URL`）；`git diff --check` clean。
- **一次性容器实验室（172.21.x.x，宿主 `br-stp` 10.99.0.1/24 + nft masquerade）**：控制面容器 `stp-cp-i4`（10.99.x.x，I3 rootfs 克隆 + ansible-core/sshpass）与两个 Agent 容器 `stp-agent-1/2`（10.99.x.x，sshd + `agentops` 用户）；bundle 由工作树在实验室宿主上组装（`release-manifest.json` 摘要用 ADR-0040 既有实现计算），站点输入 `site.yaml` + 五个绑定（DB 落在容器内 PG 的 **5433**）。
  - `validate`/`plan`/`install --dry-run` PASS（dry-run 含 `install.s5.binding`/`install.s5.plan`，零 API 调用）。
  - **首装 + S5 双 Host 接入 PASS**：`install.s0…s4`（digest/bindings/target_confirmed/…/venv_ready/env_created/templates_rendered/migration_applied/admin_created/units_installed/nginx_ready/health_ok）→ S5 对两台 Agent（`10.99.x.x`）各自 `host_reconciled`/`install_succeeded`/`agent_online`/`identity_recorded`/**`digest_matched`**/**`endpoint_recorded`**，设备项 `BLOCKED`（容器无设备），`install.s5 agents_onboarded` PASS。
  - **幂等重跑 PASS**：`.env.backend` 不轮换秘密（`env_reused`）、`venv_present`、`schema_at_head`、`admin_present`、Host 行数不变、`GET /hosts` 仍只有两行（不重复注册）。
  - **S6 降级验收 `verify` RC=0**：`auth`/`csrf`/`hosts`/`devices` PASS，`chain_completed`（Plan 3 → run 3 在 device 1 上 SUCCESS + 3 条 step trace），`watcher`/`storage`/`scan_upload_merge` BLOCKED。
  - 负例（实验室过程中实际触发并核对）：`POST /hosts` 缺 Fernet 键 → **503**；playbook 缺 `agent_api_url` → 断言拦下；`chown android` → `failed to look up user android`；`ARTIFACT_DIGEST` 写成列表 → Agent 上报空摘要；缺 `timeout_seconds` → 422 `INVALID_LIFECYCLE`；错误脚本版本 → 422 `INVALID_SCRIPT_REFS`；curl 无 Origin 写请求 → 403 CSRF（同时证明 `verify` 的 CSRF 探针有效）。
- **实验室操作事故（已修复，需收尾确认）**：首次清理半成品容器时 `rm -rf` 透过 bind 挂载删除了 238 宿主的 `/dev` 设备节点（`/dev/null` 退化为普通文件）。已用 `mknod` 按标准 major/minor 重建（null/zero/full/random/urandom/tty/console/ptmx/loop-control/tun + NVMe 与分区节点 + fd 链接），`scp`/`ssh`/容器/apt 均已恢复正常；**该机仍建议在下一次维护窗口重启一次**，让 devtmpfs 与该机其他运行期设备节点完全归一。后续脚本已加护栏：卸载不干净的挂载点时直接 abort，不再 `rm -rf`。
- **pending（功能验收）**：真机 S6（真实 ADB 设备、scan/upload/merge、存储写读探针）、Ubuntu/HTTPS/CIFS 现场适配、R2 发布端（bundle 生产/签名/渠道留痕）、I5 导航与 MS-01…MS-13 现场证据；`verify` 的 `BLOCKED` 项不得当作已验收。

## Revisit

- **站点重跑不重启常驻服务**：S2 会把新发布树落到部署根，但已运行的服务仍加载旧代码（实验室里必须手动 `systemctl restart` 才能让 `verify`/UI 用上新 schema）。安装语义是“新装”而非“升级”，当前刻意不自动重启；若现场把它当升级通道用，需要先补一次显式重启步骤或收敛到既有热更新通道。
- **全新站点需要脚本目录登记**：`verify` 在建计划前调用 `POST /api/v1/scripts/scan`（幂等）；若后续把 S4 扩为“站点可用性收口”，应把这次扫描纳入安装阶段，而不是让每个消费者各自触发。
- **ADR-0040 载荷基准与 harness 文件**：payload 目录里存在 `AGENTS.md` 与 `CLAUDE.md -> AGENTS.md`（harness 约定）。当前靠“落地保链接”保证摘要可比；更彻底的做法是把 harness 文档排除出载荷基准（两侧同改 + parity 测试），属于 ADR-0040 范围，不在本切片。
- **Agent 侧摘要只在重启后逐拍上报**：`ARTIFACT_DIGEST` 在安装脚本之后写入，Agent 于下一次心跳带上（已按有界等待处理）；若现场把“安装完立即断言”当作强约束，应把摘要写入提前到脚本内。
- 现场若用私钥 SSH：请确认私钥属主=部署账号且 0600，否则 `install` 会在 S5 绑定阶段 fail-closed（而不是让 Ansible 以 `Permission denied (publickey)` 收场）。
