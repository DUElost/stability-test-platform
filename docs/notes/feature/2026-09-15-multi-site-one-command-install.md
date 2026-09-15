# 多站点 P1/I5.5：一站式部署（`deploy/*.sh`）+ inventory 批量接 Agent

Status: implemented
Class: feature

## Decision

把「装一个新站点」从「读 ADR + 填 33 个字段 + 逐条跑命令」收敛成**三条命令**，并顺手补齐两个真实缺口（本机存储无法表达、Agent 必须先声明）。落实 P1 设计的 I5.5（§3.3/§4/**§4.2 部署契约**/§5 S1/S5）。

目标 UX（已确认）：

```bash
git clone <repo> && cd stability-test-platform
sudo ./deploy/preflight.sh          # 只读体检：逐项 PASS/FAIL/BLOCKED + Fix，零写入
sudo ./deploy/install.sh            # 构建发布物 → init（≤4 问）→ validate/plan → S0–S4
sudo ./deploy/agent/install.sh      # 读仓库外 ~/hosts.ini → S5（Host 仍由本站 API 分配）
```

- **三个薄封装**（`deploy/preflight.sh`、`deploy/install.sh`、`deploy/agent/install.sh`）只做定位仓库根、准备解释器/目录、按顺序调 `python -m tools.site_config` 与 `tools/release/build_bundle.py`；路径与默认值统一在 `deploy/lib/deploy-common.sh`（避免两个入口指向不同站点）。`tests/test_deploy_scripts.py` 守「`bash -n` 通过、不自己实现宿主机命令、不用第二语言解析报告、python 只调那两个入口、默认值只在公共库、每个入口自带 `--help`、参数级守卫排在任何宿主机写入之前」。
- **参数级守卫前移**：`verify --dry-run` 被拒绝（退出码 2，`verify` 没有 dry-run 语义，静默照跑等于违背 `--dry-run`）、`handover --dry-run` 转成 `handover --dry-run`、`verify`/`handover` 缺 `site.yaml` 直接报错——三者都发生在建 venv/目录之前，错误调用不留半成品。
- **`preflight`**（`tools/site_config/preflight.py`）本机零写入探测：平台/资源下限/命令（含 Agent 路径的 `ansible-core`、`sshpass`）/入口端口（读 `/proc/net/tcp` 的 LISTEN）/NTP/工具环境/声明的库与 Redis/绑定目录权限/发布物完整性/既有站点 marker。**每条 FAIL 带实测事实**（如「Entry ports already in use: 80, 8000.」），无输入的两项记 `BLOCKED`（不通过也不失败）。
- **`init`**（`bootstrap.py`）：探测可派生项（OS/架构/时区/主路由地址/数据盘），只问 ≤4 项（站点标识、公开入口、库名、存储路径），生成 `site.yaml`（头部注释标注每项来源：探测/默认/确认）+ 绑定目录（0700/0600：DB DSN、Redis index、首管理员口令、Fernet）。秘密只生成、只落文件；`--no-fix` 退化为只报 `sudo` 命令，`--dry-run` 零写入。
- **`build_bundle`**（`tools/release/build_bundle.py`）：R2 之前的发布物来源——工作树 → `backend/`、`deploy/`、`tools/`、`frontend/dist-prod/` + `release-manifest.json`（摘要按**路径加载 bundle 内的 `artifact_digest.py`** 计算，与 S0 同基准；`schema_target` 现算 alembic head；保持符号链接）。发布渠道就绪后只需把 `STP_BUNDLE` 指向产物。
- **inventory → `agents`**（`inventory.py`）：Ansible INI 形状（组 `[stp_agents]`/`linux_hosts` + `[stp_agents:vars]`），放**仓库外**（默认 `~/hosts.ini`）；每台一行 `ansible_user` + `ansible_password` 或 `ansible_ssh_private_key_file`；**一套共享凭据**（默认 ref `agent_ssh`，落绑定目录 0600），逐台可覆盖 `ssh_credential_ref`；**同 ref 不同秘密一律拒绝**（否则会静默覆盖另一台的凭据）。`install --agents-inventory <file>` 合并后走既有 S5，Host ID 仍由本站 API 分配。
- **「先控制面后 Agent」**（真正的阻塞点）：`agents` 允许为空（S5 跳过并打印后续命令）、`control_plane.ssh_user/ssh_credential_ref` 改可选（本地模式不需要）、同站点 `install_root` 必须一致（`STP_SCRIPT_RUNTIME_ROOT` 是站点级单值，异构根会静默取错路径）。
- **`storage.provisioning: local_mount`**（新增第三态）：本机磁盘子树没有远端身份，不写 `target/protocol/share`；把本机路径写成 NFS 分享会让生成的 `site.yaml` 说谎。角色 target 隔离随之收敛为「**有远端管理面的角色**才占 target 槽位」：`managed_linux` 存储与控制面同机仍拒绝（双重管理），`local_mount`/`existing_share` 不再被误判。
- **顺序**：发布物必须**先于**站点输入——`init` 从清单读 `expected_release`，顺序颠倒会让 `plan` 永久 `release_version_mismatch`（沙箱实测）。
- **文档**：新增 [`installation.md`](../../operations/installation.md)（全流程 + inventory 契约 + 常见 Fix 对照）并登记三处索引；设计升 v0.8（新增 §4.2 部署契约：角色/支持面/发布物/秘密/迁移/升级回滚归属/离线边界/幂等）；PRD 升 v0.10；生产最小部署清单与 Ansible README 指向新入口。

## Alternatives

- **让 `install.sh` 直接生成 `site.yaml` 模板让操作者手填**：仍是「输入墙」（33 个必填字段），且本机可派生的信息（OS/架构/时区/地址/盘）要人抄；改为探测 + 只问 4 项。
- **在 shell 里做校验/挂盘/建库**：会出现第二份实现并与 Python 侧漂移（I4 曾在 bindings 目录上踩过一次）；改为 shell 只调入口，Python 侧唯一实现。
- **preflight 顺手把缺的东西装上**：「零写入」是它的核心承诺（操作者要先看清再决定）；改为只报 Fix，`install.sh` 负责动手。
- **把「本机存储」表达为 `existing_share` + 控制面主机名**：会撞角色隔离（同机=双重管理），且要编造 `share` 路径；改为新增 `local_mount` 第三态（`target/protocol/share` 必须为空）。
- **`storage.target` 为空时也占 target 槽位**：本地存储在模型上根本没有远端身份，占用槽位会强迫操作者发明别名，反而掩盖「同一台机器」这一事实；改为只让有管理面的角色占位。
- **让 inventory 里的 Host 直接对应 `agents[].key`（跳过 `model_validate`）**：会绕过站点级约束（`install_root` 一致、路径重叠、角色隔离）；改为合并后回到同一套 Pydantic 校验，且拒绝呈现为**配置检查项**（`install.inventory`）而不是异常回溯。
- **让 Agent 先声明、再装控制面**（I1–I5 的隐含要求）：现场做不到（Agent 还没接入控制面）；改为允许空 `agents` + 后续 inventory 接入。
- **在 shell 里复制 inventory 示例**：示例键名会与 Python 侧模板漂移；改为模板由 `inventory.TEMPLATE` 生成，shell 不出现任何清单键名。

## Verification

- **仓库离线**（worktree `/tmp/stp-i55`，base=origin/main `7504a3ed`）：
  - `pytest tests/ -q --ignore=…`（同 `repo-tests` 门禁口径）→ **843 passed**（新增 `test_site_preflight.py` 16、`test_site_bootstrap.py` 15、`test_site_inventory.py` 19、`test_release_bundle.py` 16、`test_deploy_scripts.py` 24；`test_site_config.py` 新增 `local_mount`/空 `agents`/CP SSH 可选/`install_root` 一致用例，`test_site_install.py` 新增 inventory 合并与失败用例 5）。
  - `ruff check tools/` 通过；`check:quick` 见下；`tools/dev/check-internal-ip-leak.py --check` 通过（新文档/夹具一律用 RFC 5737 文档地址）；`tools/dev/check_governance_surface.py --check` 通过；`git diff --check` clean。
- **沙箱实跑**（本机，所有路径与工具 venv 都重定向到 `/tmp/i55-sandbox`，`--no-fix` 不碰宿主机）：
  - `./deploy/install.sh --no-fix --yes`：构建 bundle（`version=local-20260915-7504a3ed  schema_target=j7k8l9m0i2`）→ `init` 生成 `site.yaml` + 4 个 0600 绑定 → `validate` PASS → `plan` PASS → `install` 进到 S1。
  - 预期内的失败：S1 `install_storage`（声明路径不是挂载点，`--no-fix` 没挂盘）——正是 `deploy/install.sh --no-fix` 该有的行为。
  - `./deploy/agent/install.sh`（无 inventory）→ 写出 0600 模板并停下；填入两台共享凭据后 `--dry-run` → 解析合并成功、`install --through-agents --agents-inventory` 进入 S1，且**未**写任何凭据（dry-run）。
  - **沙箱暴露并修复的缺陷**：① `init` 早于 `build_bundle` 会让 `expected_release` 与清单版本永久不匹配（顺序调整）；② 「本机存储」在模型里无法表达（撞 `role_target_collision`）；③ `probe_data_disk` 把**生产数据盘**（有分区、sda1 挂在 `/mnt/stp-aee`）当成「未挂载整盘」建议挂载——改为只接受「无分区且有文件系统的裸盘」，其余必须显式 `--data-disk`；④ `merge_agents` 的模型拒绝会以回溯形式冒到 CLI（改为配置检查项）；⑤ inventory 派生的逻辑键以数字开头（IP 目标直接点转横杠）违反 `LogicalKey`（首字符必须字母）——改为 `agent-<target>` 派生；⑥ 坏行的回显会把裸词（可能就是口令）写进错误详情——改为只回显键名/主机名/ref。
- **本机残留**：沙箱跑会真实创建 `/opt/stp-city-b`、`stp` 用户与工具 venv（S1 在建挂载前先建账号/目录），测试后已逐项删除并复核（`id stp` 无此用户、`/opt` 无残留）。
- **pending（现场，238 不可达）**：2026-09-15 尝试执行 238 宿主验收时 ping/ARP 无响应（同网段 225–249 邻机在线，仅 `.238` 无回应，ssh 报 `No route to host`），故**本切片未取得现场证据**。恢复后按以下顺序补做（每一步的产物即证据）：
  1. `sudo ./deploy/preflight.sh`：读逐项 ✓/✗ 与 Fix（预期端口 80/8000 空闲、库/Redis 探测需显式给 URL）；
  2. `sudo ./deploy/install.sh`：站点 `city-b`（新库 `stp_b`、Redis db1、931G 盘独立子树 bind 到 `storage.mount_path`，**不动盘上既有 56G `aee_events`**）；期望 S0–S4 全 PASS、`/health` 与 `/site/` 可达、`runs=1`；
  3. `~/hosts.ini` 写入两台 Agent 容器（`stp-agent-1/2`，共享凭据）→ `sudo ./deploy/agent/install.sh`：期望两台 Host `digest_matched`、心跳新鲜；
  4. `sudo ./deploy/install.sh verify` → **RC=0**（auth/csrf/hosts/devices/chain PASS；watcher/storage/scan_upload_merge 如实 BLOCKED）；
  5. 保存 verify JSON → `sudo ./deploy/install.sh handover`：期望 MS-02/04/05/06/10/13 PASS、MS-01 视真机主链如实 BLOCKED；
  6. 负例：`preflight` 在库非空/端口占用时 FAIL 并给 Fix；坏 inventory 行 fail-closed；`install.sh --dry-run` 与 `agent/install.sh --dry-run` 零写入（对比 `install-state.json` 与绑定目录 mtime）；
  7. 重跑 `sudo ./deploy/agent/install.sh`：`runs` 递增、秘密不轮换（MS-04 证据）。

## Revisit

- **`install-state.json` 只保留最近一次运行**：`agent/install.sh` 走的是 `install --through-agents` 全量重跑（幂等重核），会把 S1–S4 一起重验。若未来要「只跑 S5」，需要新增只做 S5 的入口（当前有意不做：先重核站点再接入更安全）。
- **异构 Agent 安装根**：现以「同站点必须一致」fail-closed（`STP_SCRIPT_RUNTIME_ROOT` 单值）。真要异构需先把该键改成按 Host 渲染（后续切片）。
- **`probe_data_disk` 仍不格式化**：只建议「已带文件系统的裸盘」；已有分区的盘必须显式 `--data-disk <分区>`。若要支持自动分区/格式化，必须另做授权流程（当前明确不做）。
- **`preflight` 的端口检查只读 `/proc/net/tcp`**：容器网络下可能与宿主视角不同（238 实验为 nspawn 容器 + 桥）；现场若在容器里跑 preflight，应改看宿主视图。
- **离线 wheelhouse**：`build_bundle --wheelhouse` 已实现但未在本次沙箱验证（需要可用的包索引）；离线现场首次使用前应先跑一次并核对。
- **`.claude/skills/agent-host-onboard`** 仍描述城市 A 的 Ansible 批量路径（fleet 对齐）；多站点新站点接入应走 `deploy/agent/install.sh`。等这条路径在现场跑通后，把该 skill 的入口指向新脚本（本切片未改 skill：不在本次 scope 内）。
