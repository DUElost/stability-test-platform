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
- **S5 目标机 sudo 预检**（采纳用户建议，借鉴本机既有的 `~/sudo-setup.yml`）：在 binding 检查之后、auth/host/install 之前逐台跑 `sudo -n true`——同一套 SSH 凭据、`NumberOfPasswordPrompts=1`、口令走 `SSHPASS` 环境变量（绝不进 argv），远端用 `&& echo STP_SUDO_OK || echo STP_SUDO_FAIL` 把结论落到 stdout。免密 sudo 不可用即 FAIL（`target_sudo_unavailable`），remediation 直接给那份 playbook 的 su 配方（`su -c` + **绝对路径** `/usr/sbin/usermod`，并补 `chmod 0440` 与 `visudo -cf` 校验，比原文件更稳）；SSH 层失败记 `ssh_probe_failed` 并提示 `ssh-keyscan` + 核对指纹（安装链保持 `host_key_checking=True`）；本地缺 ssh/sshpass 记 BLOCKED `probe_not_run`，绝不当作已验证。失败即停、不创建 Host、不触发安装。
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
  - `pytest tests/ -q --ignore=…`（同 `repo-tests` 门禁口径）→ **861 passed**（新文件 `test_site_preflight.py` 21、`test_site_bootstrap.py` 23、`test_site_inventory.py` 19、`test_release_bundle.py` 18、`test_deploy_scripts.py` 26；`test_site_agents.py` 新增摘要等待 2 条，`test_site_install.py` 新增 inventory 合并与失败用例 5，`test_site_config.py` 新增 `local_mount`/空 `agents`/CP SSH 可选/`install_root` 一致）。
  - `ruff check tools/` 通过；`check:quick` 见下；`tools/dev/check-internal-ip-leak.py --check` 通过（新文档/夹具一律用 RFC 5737 文档地址）；`tools/dev/check_governance_surface.py --check` 通过；`git diff --check` clean。
- **沙箱实跑**（本机，所有路径与工具 venv 都重定向到 `/tmp/i55-sandbox`，`--no-fix` 不碰宿主机）：
  - `./deploy/install.sh --no-fix --yes`：构建 bundle（`version=local-20260915-7504a3ed  schema_target=j7k8l9m0i2`）→ `init` 生成 `site.yaml` + 4 个 0600 绑定 → `validate` PASS → `plan` PASS → `install` 进到 S1。
  - 预期内的失败：S1 `install_storage`（声明路径不是挂载点，`--no-fix` 没挂盘）——正是 `deploy/install.sh --no-fix` 该有的行为。
  - `./deploy/agent/install.sh`（无 inventory）→ 写出 0600 模板并停下；填入两台共享凭据后 `--dry-run` → 解析合并成功、`install --through-agents --agents-inventory` 进入 S1，且**未**写任何凭据（dry-run）。
  - **沙箱暴露并修复的缺陷**：① `init` 早于 `build_bundle` 会让 `expected_release` 与清单版本永久不匹配（顺序调整）；② 「本机存储」在模型里无法表达（撞 `role_target_collision`）；③ `probe_data_disk` 把**生产数据盘**（有分区、sda1 挂在 `/mnt/stp-aee`）当成「未挂载整盘」建议挂载——改为只接受「无分区且有文件系统的裸盘」，其余必须显式 `--data-disk`；④ `merge_agents` 的模型拒绝会以回溯形式冒到 CLI（改为配置检查项）；⑤ inventory 派生的逻辑键以数字开头（IP 目标直接点转横杠）违反 `LogicalKey`（首字符必须字母）——改为 `agent-<target>` 派生；⑥ 坏行的回显会把裸词（可能就是口令）写进错误详情——改为只回显键名/主机名/ref；⑦ **存储准备会递归改属主**：`prepare_storage` 原用 `ops.ensure_dir`（末尾 `chown -R root:root`），首次跑安全但**重跑 init 时 `/srv/hdd` 已是挂载点**，会把整盘既有数据的属主一次性改成 root（238 上是 71.5G 的 `aee_events`，属主 uid 1000）——新增 `Ops.ensure_plain_dir`（只建目录、不 chmod/chown），存储三处改用它，`ensure_dir` 收窄为"交给服务账号的站点自有目录"，并加了两条不变量测试（已挂载时不 mount、不 chown、既有目录原样；真实实现不动既存属主/权限）。
- **本机残留**：沙箱跑会真实创建 `/opt/stp-city-b`、`stp` 用户与工具 venv（S1 在建挂载前先建账号/目录），测试后已逐项删除并复核（`id stp` 无此用户、`/opt` 无残留）。
- **238 现场验收（城市 B 宿主，2026-09-15）**：12 核 / 7.5 GiB / 根盘 203 GiB 可用、Debian 13、本机 PG 17.11 + Redis、数据盘 `sda` 931.5 G 整盘 ext4（既有 `aee_events` ≈71.5 GiB，属主 uid 1000，**全程未被触碰**）；宿主 `stability-*` 与 nginx 均为 disabled（此前清理态）。代码经 `git bundle` 送到 `/home/debian13/stp-i55-src`（该机 GitHub 不可达，改用 LAN 传输），另 rsync `frontend/dist-prod` 与 `backend/agent/resources`。
  1. **`preflight.sh`（裸机）**：首次直接以 ImportError 回溯收场（缺陷⑧）；修复后逐项报告——`platform`/`resources`/`dependencies` PASS、`agent_commands` BLOCKED（缺 ansible-playbook/sshpass，带 Fix）、`ports` 空闲 PASS、`toolenv` FAIL（缺 pydantic/yaml/psycopg，Fix 指向 `install.sh`）、`database`/`redis` BLOCKED，并给出 `suggested entry: http://<宿主内网地址>`（路由探测）。
  2. 装 `ansible-core` + `sshpass` 后 `preflight.agent_commands` 转 PASS。
  3. **`install.sh --yes --database stp_b --data-disk /dev/sda`**：前两次 S0–S2 PASS 但 S3 `db_unreachable`（该机有 I3 残留的 `stp` 角色，密码与本轮生成的绑定不一致 → 缺陷⑨；修「binding 不覆盖」时叠加出新的不一致 → 缺陷⑩，最终改为**以既有 binding 为准** + 显式 `--reset-db-password`）；第三次起 **RC=0，S0–S4 全 PASS**（`install.s3.migrate migration_applied`、`install.s3.admin admin_created`、`install.s4.frontend frontend_served`、`install.s4.health health_ok`）。
  4. 入口与存储证据：`/` 200、`/health` 200、`/site/` 200（标题「站点导航 · 站点 city-b」）、`/site/handover.json` 404（尚未 handover，符合预期）；`mount` 显示 `/dev/sda on /srv/hdd ext4` 与 bind 到 `/srv/stp-aee`，fstab 两条（UUID + bind，均 `nofail`）。
  5. **Agent 接入**：`/root/hosts.ini` 两容器共享凭据（0600，口令在机器本地提取、未进会话）→ `agent/install.sh`。首轮 S5 的 binding/auth/host/install/heartbeat/identity/endpoint/devices 全 PASS，仅 `install.s5.digest` FAIL——bundle 缺 `backend/agent/resources`（缺陷⑪：不在 git，清单把 host-resources 算成空集合）；补 resources 重建 bundle 后重跑，第一台 `digest_matched` PASS、第二台仍 FAIL（缺陷⑫：等待判据「任一摘要非空」提前收工，`resources` 还是上一版的值——而两台容器的摘要文件当时都正确，几分钟后 Host 上报也自动 MATCH）。
  6. **S5 修复复验（容器重装场景）**：`agent/install.sh` **RC=0**，17 项 S5 检查全 PASS（含两台 `digest_matched`、`endpoint_recorded`、`devices_discovered`）——缺陷⑫ 的等待判据修复在「重装时 resources 仍是旧值」的真实场景下验证通过。
  7. **真机接入（用户指定，Ubuntu 22.04）**：先按用户要求把 **Ubuntu 22.04 纳入支持矩阵**（纳入前实测：Agent 代码在 Python 3.10 上 `compileall` 全过、5 个依赖在有依赖解析时可下载）。真机 A（Ubuntu 22.04.2、无设备）首轮 install run 失败于 `agentctl health` 的「服务器连接」——**该机没有 curl**（Ubuntu 最小安装），而安装/自检链有 4 处依赖它（缺陷⑭）；把 curl 变成显式依赖后重跑接入。真机 B（**Debian 13**、6 台 ADB 设备）被 sudo 阻塞：`android` 不在 sudoers、`root` SSH 亦不可登录 → 待用户处置后再接。
  8. **`verify`（238 现场）**：`auth`/`csrf`/`hosts`（3 台）/`navigation`/`devices` **PASS**，`watcher`/`storage`/`scan_upload_merge` 如实 `BLOCKED`；受控链 `verify.s6.chain` 逐层排查后仍 FAIL，过程中又暴露三个真缺陷（⑮⑯⑰）：
     - 现象 A：run 立即 FAIL、0 个 job → 审计明细 `admission_failed reason=script_sync_config_error`；
     - 缺陷⑮ 修复（补 env + S4 restart）后准入通过，run 进入 PRECHECK/RUNNING；
     - 现象 B：仍 FAIL，审计明细 `partial_fail: pushed=0, failed=noop: cannot map nfs_path` → 缺陷⑯（scan 不跟随 runtime_root 重新锚定 nfs_path）；
     - 缺陷⑯ 修复后**脚本推送成功**（两台容器的 `/opt/stability-test-agent/agent/scripts/noop/v1.0.0/noop.py` 就位且可直接执行），但作业仍 FAIL：`lifecycle init failed: … No such file or directory: '/opt/stp-city-b/backend/agent/scripts/noop/v1.0.0'`（**控制面**脚本根 + 目录名，缺文件名）→ 缺陷⑰；
     - 关键旁证：容器 Agent 在该时段 `active=0`、心跳正常、从未 claim 该作业 → **执行者是控制面侧的静态设备作业路径**（`job_instance.host_id=0`、`device=SYNTH-I4-02`），它用控制面 `STP_SCRIPT_ROOT` 拼 `<name>/v<ver>` 目录当可执行文件跑 → ENOENT。
  9. **待补**：真机 B（Debian 13、6 台 ADB 设备）因 `android` 不在 sudoers 待用户处置后接入；随后用**真实设备**跑 `verify`（真实设备走 Agent 侧执行、`nfs_path` 语义正确），再 `handover`；坏 inventory 与 dry-run 负例部分已取（含并发 `state_locked` 拒绝）。
- **现场暴露的缺陷（除仓库沙箱的 ①–⑦）**：⑧ `preflight` 被导入链拉崩（`__main__` 级导入 install/bootstrap 会带上 pydantic/PyYAML）——抽出 stdlib 的 `checks.py` 并让非 preflight 子命令延迟导入；⑨ 既有 DB 角色密码与生成的绑定不一致时 `init` 静默产出坏绑定——改为一律先用绑定凭据试连，不匹配即 fail-closed（`bootstrap_database_role`）并提示 `--reset-db-password`；⑩ `init` 重跑会用新口令/Fernet/DSN 覆盖既有绑定（站点 `.env.backend` 里已是首次值）——改为**以既有 binding 为准**（密码从既有 DSN 解析、admin/Fernet/Redis index 沿用），绑定只写一次并在报告里回显 `kept existing bindings (not rotated)`；⑪ `build_bundle` 允许打包缺 `backend/agent/resources` 的树（摘要必然不符）——缺即拒绝打包（`bundle_resources`），preflight 同步校验；⑫ S5 摘要等待用 `any(reported.values())` 提前收工（重装时 code 有值、resources 旧值即误判）——改为「声明项全部到齐」并把窗口从 90 s 收到 30 s（覆盖一个心跳周期 + 一次重试）；⑬ `install.sh` 无法指定站点输入项（库名/入口/存储/盘）——把 init 专属参数转发给 `init`、其余仍透传 `install`；⑮ **脚本同步键只补一半**：首装无 Agent 时 S2 省略 `STP_SCRIPT_RUNTIME_ROOT`，而模板里该键本就是注释形态；S2 的 managed 检查用**子串**比对把注释当成"键已存在"（假 PASS），且后端只在启动时读 `EnvironmentFile`、S4 用 `enable --now` 不重启已运行的服务——两处叠加使"首台 Agent 接入后再渲染"永远不会发生 → 改为按 systemd 语义解析有效键、只缺该键时**追加补齐**、S4 改 `enable` + `restart`；⑯ **scan 不跟随 runtime_root 重新锚定 nfs_path**：内容未变时跳过已存在记录，`nfs_path` 停在"首次扫描（缺该键）"写下的**控制面**路径 → 推送侧 `nfs_path_to_local` 要求 Agent 侧前缀，每次派发 `cannot map nfs_path`；改为内容未变分支也比较并重锚（`content_sha256` 不变，不算 rebaseline）；⑰ **静态设备（SYNTH-*）的作业执行路径**用控制面 `STP_SCRIPT_ROOT` 拼 `<name>/v<ver>` 目录（缺文件名）当可执行文件跑 → `No such file or directory`；容器 Agent 全程空闲、脚本已就位且可执行——属 I4 遗留的静态设备执行链，真实设备走 Agent 侧不受影响，待真机 B 接入后复核（本切片未改）；⑭ **curl 是隐式依赖**：`install_agent.sh`（API 探测 3 处）与 `agentctl.sh`（health 的服务器连通检查）都调用 curl，而依赖安装段只装 python3-venv/pip——Ubuntu 22.04 最小安装没有 curl，一次**正常的**安装被判成失败（Agent 服务已 active、health 只因缺 curl 报「无法连接」）→ 依赖段统一收集缺失项（python3-venv/python3-pip/curl）后一次装上，Debian 与 RHEL 两支都改。

## Revisit

- **`install-state.json` 只保留最近一次运行**：`agent/install.sh` 走的是 `install --through-agents` 全量重跑（幂等重核），会把 S1–S4 一起重验。若未来要「只跑 S5」，需要新增只做 S5 的入口（当前有意不做：先重核站点再接入更安全）。
- **异构 Agent 安装根**：现以「同站点必须一致」fail-closed（`STP_SCRIPT_RUNTIME_ROOT` 单值）。真要异构需先把该键改成按 Host 渲染（后续切片）。
- **`probe_data_disk` 仍不格式化**：只建议「已带文件系统的裸盘」；已有分区的盘必须显式 `--data-disk <分区>`。若要支持自动分区/格式化，必须另做授权流程（当前明确不做）。
- **`preflight` 的端口检查只读 `/proc/net/tcp`**：容器网络下可能与宿主视角不同（238 实验为 nspawn 容器 + 桥）；现场若在容器里跑 preflight，应改看宿主视图。
- **离线 wheelhouse**：`build_bundle --wheelhouse` 已实现但未在本次沙箱验证（需要可用的包索引）；离线现场首次使用前应先跑一次并核对。
- **pip 镜像**：本机 `~/sudo-setup.yml` 还会给目标机配 `/etc/pip.conf` 与 `~/.pip/pip.conf`（清华源）。站点安装的 venv 目前走默认源（238 上够快）；现场 PyPI 慢的站点可借鉴这一步——未纳入 `install.sh`（需要现场证据后再决定）。
- **`.claude/skills/agent-host-onboard`** 仍描述城市 A 的 Ansible 批量路径（fleet 对齐）；多站点新站点接入应走 `deploy/agent/install.sh`。等这条路径在现场跑通后，把该 skill 的入口指向新脚本（本切片未改 skill：不在本次 scope 内）。
