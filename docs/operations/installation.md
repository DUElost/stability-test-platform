# 独立站点安装（一站式部署：preflight → install → agent → verify → handover）

适用：**多站点 P1**（每站点独立控制面/数据库/Redis/中心存储/Agent）。本文是三个入口脚本的权威说明；站点配置字段、阶段语义与验收映射分别见
[`多站点安装设计`](../design/2026-09-multi-site-installation.md)、[`站点导航与交接`](./site-handover-and-navigation.md)。

```bash
git clone <repo-url> stability-test-platform && cd stability-test-platform
sudo ./deploy/preflight.sh          # 只读体检：逐项 ✓/✗ + Fix，零写入
sudo ./deploy/install.sh            # 装控制面：只回答 ≤4 个问题
sudo ./deploy/agent/install.sh      # 读 ~/hosts.ini，批量接入 Agent
sudo ./deploy/install.sh verify     # 受控验收（S6 降级路径）
sudo ./deploy/install.sh handover   # 汇总 P1 验收证据
```

三个脚本都是**薄封装**：只定位仓库根、准备工具环境、按顺序调
`python -m tools.site_config` 与 `tools/release/build_bundle.py`。校验、挂盘、建库、
渲染等逻辑全在 Python 侧，shell 里没有第二份实现（不变量由
`tests/test_deploy_scripts.py` 守）。

---

## 1. 角色与前置

| 角色 | 说明 | 本次安装是否本机 |
|------|------|------------------|
| 控制面 | 本站 API/前端/Nginx/systemd 单元 | 是（安装器必须在 `control_plane.target` 上执行） |
| 中心存储 | Agent 回传事件与产物的存放点 | 默认**本机磁盘子树**（`local_mount`），可换远端 NFS/CIFS |
| Agent | 连设备的主机（Linux + ADB） | 否（经 SSH 由 `deploy/agent/install.sh` 接入） |

前置条件（`preflight.sh` 会逐条核对）：

- Debian 13 或 Ubuntu 22.04 / 24.04、x86_64、systemd；
- ≥2 核 / ≥4 GiB RAM / 根文件系统 ≥20 GiB 可用；
- `python3`、`nginx`、`systemctl`；接 Agent 另需 `ansible-core` 与 `sshpass`；
- 入口端口 80（HTTPS 另需 443）空闲；
- 主机时钟已 NTP 同步（审计与租约时间要对得上）；
- PostgreSQL 可达（`init` 可在本机建空库与角色，需 `sudo -u postgres` 可用）；
- Redis 可达（每站点一个独立 db index）。

**不支持的入口**：Kubernetes、把开发 Compose 当生产、公网 `curl | bash`。发布物是
`git clone` 出来的工作树（R2 发布渠道就绪后改用预置 bundle，见 §7）。

## 2. `preflight.sh`：先看清，再动手

```bash
sudo ./deploy/preflight.sh                                   # 本机就绪度
sudo ./deploy/preflight.sh --db-url postgresql+psycopg://stp:***@127.0.0.1:5432/stp_b
sudo ./deploy/preflight.sh --redis-url redis://127.0.0.1:6379/1
sudo ./deploy/preflight.sh --bundle /srv/stp-bundle --bindings-dir /etc/stp/bindings
```

- **零写入**：不建 venv、不落文件、不重启服务、不连生产库（只连你显式给的目标）。
- `PASS` / `FAIL` / `BLOCKED` 三态：`BLOCKED` = 没给它输入所以没验证，不算失败，也不等于通过。
- 每条 `FAIL` 都带实测事实与 `Fix:` 行，例如「Entry ports already in use: 80, 8000.」。
- 退出码 `0` = 无 FAIL，`1` = 有 FAIL，`2` = 参数错误。

## 3. `install.sh`：装控制面

```bash
sudo ./deploy/install.sh            # 交互：只问站点标识、公开入口、DB、存储（各带探测默认值）
sudo ./deploy/install.sh --yes      # 全部取探测默认，非交互
sudo ./deploy/install.sh --dry-run  # 只报计划，一个字节都不写
sudo ./deploy/install.sh --no-fix   # 宿主机写操作（venv/建库/挂盘/fstab）只报命令
sudo ./deploy/install.sh --database stp_b --public-url http://192.0.2.5 --data-disk /dev/sda
```

站点输入项直接传给 `init`：`--display-name`、`--public-url`、`--database`、`--redis-index`、
`--storage-mount`、`--data-disk`、`--admin-username`、`--bundle`、`--reset-db-password`（等价环境
变量见 §8）；其余选项透传给 `install`（如 `--through-agents`、`--agents-inventory`）。

**既有数据库角色**：`init` 发现同名角色时**不会**改它的密码——先用本次生成的凭据试连一次，
连不上就 fail-closed 并提示：要么给 `--reset-db-password`（显式允许 `ALTER ROLE`，适用于
「这台机器上是我上次装的残留」），要么换库名/角色。若本机没有 `psycopg`/`psql` 无法试连，
`init` 会记一条 action 提示后续注意点。

内部顺序（任一步失败即停，产物保留，重跑从缺的那步继续）：

| 步 | 动作 | 产物 |
|----|------|------|
| 1 | 构建发布物（`tools/release/build_bundle.py`） | `$STP_BUNDLE` + `release-manifest.json` |
| 2 | 生成站点输入（`init`） | `$STP_SITE_FILE`（`/etc/stp/site.yaml`）+ 绑定目录（0700/0600） |
| 3 | `validate` + `plan` | 校验与计划（脱敏输出） |
| 4 | `install`（S0–S4） | 部署根、systemd 单元、Nginx、`/health`、`/site/` |

**为什么发布物先于站点输入**：`init` 会从清单读取 `expected_release`，顺序颠倒会让
`plan` 永久报 `release_version_mismatch`。

**首管理员口令**：`init` 生成一次性口令，**只在绑定目录里**（`site_admin`，0600），
终端只打印该文件路径。请立即改密并妥善保管该文件；报告与日志不含口令。

**宿主机写操作**（默认执行并逐项回显）：

- 建工具环境 `/opt/stp-tool`（pydantic/pyyaml/psycopg）；
- 建空库与角色（`sudo -u postgres`），只**建**空库，绝不迁移或清空既有库；
- 挂数据盘到 `/srv/hdd` + `bind` 站点子树到 `storage.mount_path` + 追加 fstab（`nofail`）；建目录只创建缺失项，**不改既有文件/目录的属主与权限**（重跑时挂载点上的既有数据保持原样）。

**绝不会做**：格式化磁盘、删除既有数据目录、递归改既有数据的属主、覆盖未接管的数据库、`--force` 绕过保护。

### 中心存储：站点自建导出（`export_to_agents`）

`init` 为 `local_mount` 站点写 `storage.export_to_agents: true`：控制面把
`storage.mount_path` 这一棵子树以 NFS 导出给本站 Agent，Agent 的 AEE 落点才有中心存储。
`install` 的两步（任一步失败即停）：

- **S1**：装 `nfs-kernel-server`；把导出根交给约定写入身份（`chown root:1000` + `chmod 0775`，
  只动这一层，不递归）；
- **S2**：写 `etc/exports.d/stp-<site>.exports`（客户端 = 声明 Agent 的 `/24`；目标写主机名
  时放宽为 `*`，因为推不出网段）→ `exportfs -ra` → `enable --now nfs-server`。
  首装尚无 Agent 时导出文件为空，报告如实标 `export_deferred`；加 Agent 后重跑即生效。

Agent 侧（`agent/install.sh` 与 Ansible playbook）会建好 AEE 本地根、挂载点，挂载站点导出，
并把 `MOUNT_POINTS` 跟随中心存储路径——**这是 `/storage` 页与 S5 存储断言的判据**；挂载失败
只降级为提示，但 `install --through-agents` 会以 `install.s5.storage` 如实报 FAIL。

**边界**：外部分享/受管存储（`existing_share` / `managed_linux`）由对方导出，本站不代挂；
Agent 侧的挂载在这些站点由运维按分享约定自行完成，S5 对该项记 `BLOCKED export_not_enabled`。

### 监控栈：`/storage` 页的数据源（`monitoring`）

`init` 写 `monitoring.enabled: true`（默认装）：站点自带 Prometheus + node-exporter，
`/storage` 页才有数据。连接面全部在回环，站点入口不暴露任何指标端口：

- **S1**：装发行版包 `prometheus` 与 `prometheus-node-exporter`；
- **S2**：渲染抓取配置 `/etc/stp/prometheus/prometheus.yml`（job `file-server` → `127.0.0.1:9100`）、
  监听参数 `/etc/default/prometheus`（`--web.listen-address=127.0.0.1:<prometheus_port>`，默认 9091）
  与 `/etc/default/prometheus-node-exporter`（回环 + `--collector.nfsd` + textfile 采集器）；
- **S4**：落地宿主进程内存采样器（`/usr/local/sbin/stp-mem-top` + `stp-mem-top.timer`）、
  `enable --now` 三个单元，并实测 `http://127.0.0.1:<端口>/-/ready` 才报 PASS。

两个不变量：

- **端口与 job 名就是后端默认值**——`STP_PROMETHEUS_URL` 未设时后端查 `127.0.0.1:9091`，
  `STP_CONTROL_PLANE_NODE_JOB` 未设时用 `file-server`。改这两处要同步站点后端环境，
  否则页面会静默空掉（装监控栈本就是为了它）。
- **发行版 unit 必须以 `$ARGS` 读 `/etc/default`**：读不到时安装器在写任何配置之前 FAIL
  （`install_monitoring`）。否则我们写的启动参数会被静默忽略，Prometheus 退回发行版默认
  端口与自带配置，页面空着而安装报告是绿的。

不想装监控栈的站点：把 `monitoring.enabled` 置 `false` 后重跑（安装面最小化），
代价是 `/storage` 页没有数据源。

## 4. `agent/install.sh`：按 inventory 接 Agent

```bash
sudo ./deploy/agent/install.sh                       # 默认读 ~/hosts.ini
sudo ./deploy/agent/install.sh --inventory /etc/stp/hosts.ini
sudo ./deploy/agent/install.sh --dry-run             # 只校验 inventory 与绑定，不接
```

首次运行会写出清单模板（0600）后停下；填好再跑一次。清单格式即 Ansible INI，
放在**仓库外**（默认 `~/hosts.ini`）：

```ini
[stp_agents]
192.0.2.11 ansible_user=ops ansible_password=<secret>
192.0.2.12 ansible_user=ops ansible_password=<secret>

[stp_agents:vars]
install_root=/opt/stability-test-agent
local_aee_root=/var/stp-aee
```

- 每台一行：`ansible_host`（默认取行首主机）、`ansible_user`，以及
  `ansible_password` **或** `ansible_ssh_private_key_file`；
- 可选逐台覆盖：`ansible_port`、`agent_key`、`install_root`、`local_aee_root`、`ssh_credential_ref`；
- **一套共享凭据**：所有主机默认用同一个绑定 `agent_ssh`，脚本把清单里的凭据写进
  `$STP_BINDINGS_DIR`（0700/0600）。需要**另一种**凭据（另一个用户、另一种类型、
  另一个秘密）的主机必须自带 `ssh_credential_ref`——同 ref 不同秘密会被拒绝，
  因为那会静默覆盖另一台机器的凭据；
- 同站点 `install_root` 必须一致（`STP_SCRIPT_RUNTIME_ROOT` 是站点级单值）；
- **Host ID 始终由本站 API 分配**，清单不写 Host ID；Host 名固定 `<site.id>-<agent_key>`；
- Agent 机器上的安装动作由控制面下发的既有安装链完成，脚本不直接改 Agent 配置。

## 5. `verify` 与 `handover`

```bash
sudo ./deploy/install.sh verify                       # 登录/CSRF、Host/设备、noop 受控链
sudo ./deploy/install.sh handover                     # 读安装记录 + verify 报告
```

`handover` 默认读 `$STP_STATE_DIR/verify-report.json`（若存在）。要让它拿到这次 verify
的结果，保存同一份报告即可：

```bash
sudo /opt/stp-tool/bin/python -m tools.site_config verify --config /etc/stp/site.yaml \
    --bindings-dir /etc/stp/bindings --json > /var/lib/stp/verify-report.json
sudo ./deploy/install.sh handover
```

存储写读探针需要**显式授权**：`verify --storage-probe-subdir <name>` 会在
`<storage.mount_path>/<name>/` 下写一个探针文件、原样读回、只删这一个文件；路径不是挂载点
（或未授权）时如实 `FAIL`/`BLOCKED`，绝不写进本机同名目录冒充共享存储。scan/upload/merge
仍是 `BLOCKED`（需要真实设备日志），不得当作已验收。

两个细节：

- `install.sh verify --dry-run` 会被**拒绝**（退出码 2）：`verify` 本来就要驱动受控链，没有
  dry-run 语义；只想「看就绪度」用 `preflight.sh`，想限时用 `--run-timeout`。
- `install.sh handover --dry-run` 会转成 `handover --dry-run`（只报告、不写 `handover.json`）。
- `verify` / `handover` 都要求站点输入已存在：缺 `site.yaml` 时脚本直接报错（不会留下
  半成品目录），提示先跑 `install.sh`。

## 6. 幂等、重跑与断点

- 重跑逐项**重核实际状态**，不信任记录：已存在的秘密不轮换，已存在的目录/账号不重建；
- **绑定文件只写一次**：`init` 重跑保留既有 `site_admin` / `site_ssh_encryption` / DSN（输出里
  标 `kept existing bindings (not rotated)`）——站点 `.env.backend` 里已是首次生成的值；
- 站点级秘密（JWT/Agent secret/WS token/Fernet）只在首次生成；重跑 `env_reused`；
- `install-state.json`（0700 目录、0600 文件、flock 互斥）记录阶段与 `runs` 计数；
- 同站点并发安装被拒绝（`state_locked`）；`--dry-run` 只验证与规划；
- 部分完成后重跑从缺的阶段继续；不要为了「干净重装」删除部署根——先 `mv` 到一边。
- **升级已装站点时 `--dry-run` 会报 `install.s3.schema` FAIL**：dry-run 不落新树，而该检查
  比的是部署根里那份 alembic 的 head（仍是旧树）与发布物清单的 `schema_target`。这是
  dry-run 的已知局限，不是配置错——正式跑（不带 `--dry-run`）会先落新树再比对；

## 7. 离线与切换发布物

- 发布物目录由 `$STP_BUNDLE`（默认 `/srv/stp-bundle`）决定；已由发布渠道备好时把它指过去即可跳过第 1 步；
- 目标机无外网时，发布物需带 `wheelhouse/`（`build_bundle.py --wheelhouse`）并声明
  `network.dependency_mode: offline`；
- 前端 `dist-prod` 缺失时构建会报 `bundle_frontend` 并提示
  `cd frontend && npm ci && npm run build:prod`：现场没有 node 时改用预置 bundle。

## 8. 覆盖位置（环境变量）

| 变量 | 默认 | 用途 |
|------|------|------|
| `STP_SITE_FILE` | `/etc/stp/site.yaml` | 站点输入 |
| `STP_BINDINGS_DIR` | `/etc/stp/bindings` | 绑定目录（0700/0600） |
| `STP_STATE_DIR` | `/var/lib/stp` | 安装记录与计划报告 |
| `STP_BUNDLE` | `/srv/stp-bundle` | 发布物与清单 |
| `STP_TOOL_VENV` | `/opt/stp-tool` | 工具环境 |
| `STP_AGENTS_INVENTORY` | `~/hosts.ini` | Agent 清单（仓库外） |
| `STP_SITE_ID` | `city-b` | 首次交互提问的默认站点标识 |

三个脚本共用同一份默认值（`deploy/lib/deploy-common.sh`）：**不要**在某一个脚本里
单独改路径，否则 `install.sh` 与 `agent/install.sh` 会指向不同站点。

## 9. 常见 Fix 对照

| 失败码 | 含义 | Fix |
|--------|------|-----|
| `preflight_ports` | 80/443/8000 被占用 | `ss -ltnp` 找到占用进程后停用，再重跑 |
| `install_dependency` | 缺 python3/nginx/systemctl | 按站点 profile 装基础包 |
| `agent_path_commands_missing`（BLOCKED） | 缺 ansible/sshpass | `apt install -y ansible-core sshpass` |
| `preflight_time` | 时钟未同步 | `systemctl enable --now systemd-timesyncd` |
| `preflight_toolenv` | 工具环境缺依赖 | 直接跑 `deploy/install.sh`（它会自建） |
| `bootstrap_database_role` | 同名角色已存在且密码不同 | 加 `--reset-db-password`（允许 `ALTER ROLE`）或换库名/角色 |
| `db_unreachable` / `db_unmanaged` | 数据库拒绝绑定 / 非空且非本平台 | 先看是不是上面那条（既有角色）；其余用空库，非空库需人工裁决，不得清空 |
| `preflight_redis` | Redis 未回 PONG | 修通 Redis 或换 db index |
| `bundle_frontend` | 前端产物缺失 | `cd frontend && npm ci && npm run build:prod` |
| `bundle_resources` | `backend/agent/resources` 缺失（不在 git） | 从构建机/发布渠道带上该目录（230MB 工具集），再打包 |
| `bundle_revision` | 不是 git 工作树 | 显式 `--revision` 或改用预置 bundle |
| `inventory_user_missing` | 清单缺 `ansible_user` | 补该行或写进 `[stp_agents:vars]` |
| `inventory_credential_missing` | 清单缺凭据 | 补 `ansible_password` 或 `ansible_ssh_private_key_file` |
| `inventory_credential_conflict` | 同 ref 不同秘密 | 给其中一台自己的 `ssh_credential_ref` |
| `inventory_shape` | 清单键名/键值形状不对 | 只用文档列出的键；值不含空格 |
| `agent_install_root_mismatch` | 清单与站点声明的安装根不一致 | 统一 `install_root`（站点级单值） |
| `install_storage` | 声明路径不是挂载点 | 先挂盘/bind，或 `--data-disk` 让 `init` 处理 |
| `agent_install_canceled` | 安装被取消（不是脚本失败）：安装作业跑在有界窗口里（SAQ 900s），目标机首次 `apt update`/装包慢会超窗；显式取消也是同一终态 | 看 RunConsole 日志尾部确认卡在哪一步，复跑安装（主机页按钮或 `deploy/agent/install.sh`）；反复被取消就在目标机**预装大件**（如 `nfs-common`）再触发——这是绕过，终态出口见 #2220（窗口可配 / 作业与 console 解耦） |
| `install_export` | 装 NFS 服务端或 `exportfs -ra`/`nfs-server` 失败 | 看 `dpkg -l nfs-kernel-server`、`exportfs -s`、`systemctl status nfs-server` 输出 |
| `shared_storage_not_mounted` | Agent 没挂上中心存储（或 verify 时路径不是挂载点） | Agent 侧 `findmnt <mount_path>`、`mount -t nfs <站点入口>:<mount_path> <mount_path>`；控制面侧 `exportfs -s`、`systemctl status nfs-server`。从没挂上的分享不会被写进 fstab |
| `storage_unwritable` / `storage_probe_failed` | 分享拒绝写入 / 读回不一致 | 查导出选项（`all_squash` 映射身份与导出根属组）、空间与控制面到存储的链路 |
| `storage_probe_subdir` | 探针子目录取值含斜杠或穿越 | 传单个目录名（字母/数字/点/下划线/短横线） |
| `install_monitoring` | 监控栈没起来：包装不上 / 发行版 unit 不读 `$ARGS` / `/-/ready` 未就绪 | `dpkg -l prometheus prometheus-node-exporter`、`grep -n ARGS /usr/lib/systemd/system/prometheus.service`、`systemctl status prometheus prometheus-node-exporter stp-mem-top.timer`、`journalctl -u prometheus -n 50` |
| `install_confirm` / `install_hostname` | 确认值或主机名对不上 | 确认在目标机上执行，`--confirm-site/--confirm-target` 取自 `site.yaml` |
| `state_locked` | 同站点已有安装在进行 | 等它结束，或确认无残留进程后重跑 |

## 10. 相关文档

- 设计（字段规则、阶段语义、部署契约）：[`2026-09-multi-site-installation.md`](../design/2026-09-multi-site-installation.md)
- 站点导航与交接清单：[`site-handover-and-navigation.md`](./site-handover-and-navigation.md)
- 生产最小部署检查单：[`production-minimum-deployment-checklist.md`](../production-minimum-deployment-checklist.md)
- Ansible 批量（旧入口，仍可用于专项动作）：[`tools/ansible/README.md`](../../tools/ansible/README.md)
