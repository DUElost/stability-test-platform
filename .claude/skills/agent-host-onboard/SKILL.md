---
name: agent-host-onboard
description: 新 Linux Agent 主机接入 SOP。触发时机：扩容新 host、替换故障机、批量上线新机、storage 页新机「中心存储未挂载」或「设备日志盘未上报」。
---

# 新 Agent Host 接入 SOP（v0 骨架）

> **状态**：v0 骨架——§0–§7 已在 **2026-08-28 14 台批量接入**（新网段子集，具体机位
> 见 `/home/debian13/hosts.ini`）真机校准；标 ✅ 的步骤可照抄，标 ⚠️待校对的步骤在
> **下一次新机接入时**以当天实况复核后移除标记。
>
> **与 `control-plane-deploy` 的分工**：本 skill 覆盖「从裸机到 fleet 对齐」；热更新 /
> 控制面升级 / scan / 回滚见 `control-plane-deploy`。
>
> 权威细节：
> - `docs/linux-agent-ansible-runbook.md`（Ansible 入口）
> - `docs/production-minimum-deployment-checklist.md` §4（`.env` 模板）
> - `docs/design/2026-storage-roles-and-aliases.md`（存储角色）
> - `docs/operations/agent-version-and-hot-update.md`（热更新 env 同步规则）

## 0. 前置确认（只读）

```bash
# 控制面健康（本机即生产控制面时）
systemctl is-active stability-backend
curl -s http://127.0.0.1:8000/health

# 凭据注入（CLI 脚本）
set -a && . ./.env.backend && set +a
```

- 挑一台**已上线老机**作参照（双盘 / 单盘各挑一台，机位见 `/home/debian13/hosts.ini`）：
  ```bash
  ssh android@<ref-ip> 'lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT; grep -E "STP_AEE|MOUNT_POINTS|API_URL|HOST_ID" /opt/stability-test-agent/.env'
  mount | grep stp-aee
  ```
- 新机 SSH：`ssh -o ConnectTimeout=5 android@<new-ip> 'hostname; uname -a'` ✅
- `hosts.ini`（`/home/debian13/hosts.ini`）与 Ansible inventory 为**本地敏感文件**，
  不入 git；`.claude/settings.json` deny 直接 Write/Edit——改 inventory 须人工确认或
  用 ansible `-i` 临时 inventory。⚠️待校对

## 1. SSH 密钥与清单

```bash
# 免密 SSH（控制面运维机 → 新机）
ssh-copy-id -o StrictHostKeyChecking=accept-new android@<new-ip>

# 验证 sudo（首次安装前可能仍需密码；install_agent.sh 装完会落 NOPASSWD sudoers.d）
ssh android@<new-ip> 'sudo -n true && echo sudo_ok || echo sudo_needs_password'
```

- 将新机加入 `/home/debian13/hosts.ini` `[android]` 段（格式见 AGENTS.md）。
- UI 建 host 时后端会 best-effort `ssh-keyscan`；失败不阻塞建库，但后续安装/热更新前须补：
  ```bash
  ssh-keyscan -p 22 <new-ip> >> ~/.ssh/known_hosts
  ```

## 2. 存储准备（对齐老 fleet）

> **原则**：先 `lsblk` 分类，再决定走双盘还是单盘路径。`batch_hot_update` **不会**改
> fstab——挂载必须在 Agent 安装前或后手工完成。

### 2.1 磁盘分类

| `lsblk` 特征 | 类型 | `STP_AEE_LOCAL_ROOT` | 参照老机 |
|---|---|---|---|
| NVMe 系统盘 + **~931G** `/dev/sda`（常为 NTFS/未分区） | 双盘 | `/mnt/hdd/aee_events` | 双盘老机 |
| 仅 NVMe（无第二块 ~1TB 盘） | 单盘 | `/mnt/hdd/aee_events`（落在系统盘 FS 上） | 单盘老机 |

```bash
ssh android@<new-ip> 'lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT'
```

### 2.2 双盘：格式化 1TB HDD（破坏性）

> **仅当**确认 `/dev/sda`（或第二块 ~931G 盘）为数据盘、且不含需保留数据时执行。

```bash
# 在目标机上以 root 执行（示例 /dev/sda）
sudo wipefs -a /dev/sda
echo -e 'g\nn\n\n\n\nw' | sudo fdisk /dev/sda    # GPT 单分区；勿用 parted（新机常未装）
sudo mkfs.ext4 -L stp-hdd /dev/sda1
sudo mkdir -p /mnt/hdd
UUID=$(sudo blkid -s UUID -o value /dev/sda1)
echo "UUID=$UUID /mnt/hdd ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount -a
sudo mkdir -p /mnt/hdd/aee_events
sudo chown -R android:android /mnt/hdd
```

- ✅ 2026-08-28：12 台双盘新机均走此路径；格式化后 storage 页日志盘 ~916GB。
- ⚠️ 设备节点名因机型而异（`sda`/`sdb`），**必须以 `lsblk` 为准**，勿盲抄。

### 2.3 单盘：创建 AEE 目录

```bash
ssh android@<new-ip> 'sudo mkdir -p /mnt/hdd/aee_events && sudo chown -R android:android /mnt/hdd'
```

### 2.4 中心存储 NFS 挂载

对照老机 fstab / `mount` 输出。当前过渡部署（2026-08-28 校准）：

```bash
# 参照值（以老机实际 mount 为准，勿硬编码 UNC）
# <NFS 服务器>:/mnt/stp-aee  →  /mnt/stp-aee（具体地址见 hosts.ini / 老机 fstab）
sudo mkdir -p /mnt/stp-aee
# 将老机 /etc/fstab 中 stp-aee 行复制到新机，或：
# echo '<nfs-server>:/mnt/stp-aee /mnt/stp-aee nfs defaults,_netdev,nofail 0 0' | sudo tee -a /etc/fstab
sudo mount -a
mount | grep stp-aee
```

## 3. 控制面登记 Host

`HOST_ID` = IPv4 点转横杠（如 `10.0.0.89` → `10-0-0-89`），由 `allocate_host_id()` 生成，
**必须与 Agent `.env` 一致**。

### 方式 A：UI（推荐）

1. 「主机管理」→「添加主机」：填 name / IP / SSH 用户 `android` / 密码
2. 记下分配的 `id`（应与 IP 派生一致）

### 方式 B：API（批量）

```bash
# 需 admin token；AGENT_SECRET 头可绕 CSRF（见 AGENTS.md）
TOKEN=$(curl -s -H "X-Agent-Secret: $AGENT_SECRET" \
  -F "username=$STP_ADMIN_USER&password=$STP_ADMIN_PASSWORD" \
  http://127.0.0.1:8000/api/v1/auth/token | jq -r .access_token)

curl -s -X POST http://127.0.0.1:8000/api/v1/hosts \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"<hostname>","ip":"<new-ip>","ssh_port":22,"ssh_user":"android","ssh_auth_type":"password","ssh_password":"<pwd>"}'
```

验证：`GET /api/v1/hosts` 中新机 `id` 与 IP 派生号一致。

## 4. Agent 首次安装

### 4.1 Ansible CLI（批量运维首选）

```bash
export REPO_ROOT=/home/debian13/stability-test-platform
cd "$REPO_ROOT/tools/ansible"

# agent_api_url 走 Nginx 入口（:80），不直连 loopback :8000
ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml \
  --limit <new-ip> \
  -e agent_api_url=http://<控制面地址> \
  -e agent_host_id=<host_id>
```

- `agent_host_id` **必须**与 DB `hosts.id` 一致；不传则 install 脚本自行生成，易与 DB 错位。
- `AGENT_SECRET` 自动从仓库根 `.env.backend` 读取（**不是** `backend/.env`）。
- `install_agent.sh` 装完自动写 `/etc/sudoers.d/stability-test-agent` NOPASSWD ✅
- pip 镜像：默认公网 PyPI。`STP_AGENT_PIP_INDEX_URL` 设清华等镜像时，2026-08-28 曾遇
  **403**——新机接入建议留空。✅

### 4.2 UI 按钮

主机行 `status != ONLINE` 时「首次安装」→ `POST /api/v1/hosts/{id}/install`（SAQ 异步）。
日志：`$STP_INSTALL_LOG_DIR/install_<host_id>_<ts>.log`（默认 `/tmp/stp-install-logs/`）。

### 4.3 安装后检查

```bash
ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit <new-ip>
```

## 5. `.env` 对齐（装后必查）

`install_agent.sh` 生成的 `.env` **不完整**——须对照老机补全存储键。
热更新的 `PROTECTED_ENV_KEYS` 保护这些键，**不会**自动写入。

在 `/opt/stability-test-agent/.env` 确认（值以老机为准）：

```env
API_URL=http://<控制面地址>
HOST_ID=<host_id>   # 点转横杠，见 §3
AUTO_REGISTER_HOST=false

STP_AEE_LOCAL_ROOT=/mnt/hdd/aee_events
STP_AEE_NFS_ROOT=/mnt/stp-aee
STP_NFS_ROOT=/mnt/stp-aee
STP_AEE_CIFS_ROOT=/mnt/stp-aee
MOUNT_POINTS=/mnt/stp-aee
```

**禁止误配**：

| 键 | 说明 |
|---|---|
| `ANDROID_ADB_SERVER_PORT=5039` | **仅 WSL 联调**；Linux 生产 host 用默认 **5037**，误配会导致设备数为 0 或 DEGRADED |
| `AUTO_REGISTER_HOST=true` | 生产禁用；`HOST_ID` 须与 DB 对齐 |
| 留空 `STP_AEE_LOCAL_ROOT` | AEE Reconciler 不启动，storage 页日志盘「未上报」 |
| 留空 `MOUNT_POINTS` | storage 页中心存储「未上报」（即使 NFS 已 mount） |

改完后：

```bash
sudo systemctl restart stability-test-agent
# 或 POST /api/v1/hosts/{id}/reload-config（仅 env 热读项；schema 变更仍须 restart）
```

## 6. Fleet 代码对齐（热更新）

新机安装后 `agent_code_revision` 可能落后于 fleet。对齐到控制面当前 revision：

```bash
# 单机：UI「热更新」或
curl -s -X POST "http://127.0.0.1:8000/api/v1/hosts/<host_id>/hot-update" \
  -H "Authorization: Bearer $TOKEN"

# 全 fleet 对齐（无 --limit；会扫全部 ONLINE host）
cd /home/debian13/stability-test-platform
PYTHONPATH=. venv/bin/python backend/scripts/batch_hot_update.py --direct
# 或 API 路径（需 STP_ADMIN_PASSWORD）：backend/scripts/batch_hot_update.py
```

- 期望：新机 `agent_code_revision` == 控制面 `get_agent_code_version()` 短 SHA。
- 详细门控顺序见 `control-plane-deploy` §4 与 `agent-version-and-hot-update.md` §2。

## 7. 验收清单

| 检查项 | 命令 / 入口 | 期望 |
|---|---|---|
| systemd | `ssh android@<ip> systemctl is-active stability-test-agent` | `active` |
| agentctl | `ssh android@<ip> sudo /opt/stability-test-agent/agentctl health` | rc=0 |
| 平台主机态 | `GET /api/v1/hosts` 或 UI 主机页 | `ONLINE` + `HEALTHY`（或至少 `ONLINE`） |
| 中心存储挂载 | `http://<控制面地址>/storage` 或 `GET /api/v1/stats/file-server` | nfs=已挂载；fleet `agents_mounted == agents_total` |
| 设备日志盘 | 同上 storage 页 Agent 表 | 路径 `/mnt/hdd/aee_events`；双盘 ~916GB，单盘 ~225GB |
| ADB | 接设备后 `adb devices`（agent 用户） | 单 server、5037；无 `adb_multiple_servers` DEGRADED |
| 代码 revision | 主机行 code sync 徽章 | `matched` |

```bash
# storage API 快查（admin token）
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/stats/file-server \
  | jq '{status, agents_mounted, agents_total, alerts: .alerts | length}'
```

## 8. 批量接入顺序（多台新机）

1. 并行：SSH 密钥 + `lsblk` 分类
2. 串行或分批：HDD 格式化（破坏性，逐台确认盘符）
3. 并行：NFS fstab + mount
4. 批量：API/UI 建 host
5. 金丝雀：`install_agent.yml --limit <1台>` → 验收 §7 全绿
6. 批量：剩余 `install_agent.yml` → 补 `.env` → 热更新
7. storage 页 + `GET /api/v1/hosts` 全量确认

灰度分组见 `tools/ansible/inventory.example.ini`（`agent_canary` → `agent_prod`）。

## 9. 已知坑速查

| 坑 | 处置 |
|---|---|
| `parted: command not found` | 用 `fdisk` 建 GPT 分区（§2.2） ✅ |
| pip 清华镜像 403 | 清空 `STP_AGENT_PIP_INDEX_URL`，走公网 PyPI ✅ |
| `STP_AEE_LOCAL_ROOT` 未配 / 不可写 | Agent 启动 WARN；AEE Reconciler 不工作 → 补 §5 env + restart ✅ |
| storage 中心存储「未上报」 | 补 `MOUNT_POINTS=/mnt/stp-aee`（NFS 已 mount 也会未上报） ✅ |
| 双 ADB server → DEGRADED | `adb kill-server`；统一 5037；勿留 `ANDROID_ADB_SERVER_PORT=5039` ✅ |
| `HOST_ID` 与 DB 不一致 | 心跳正常但拉不到任务 → 改 `.env` 对齐 DB `hosts.id` |
| `AGENT_SECRET` 取自 `backend/.env` | 集体 SocketIO 认证失败 → 只用 `.env.backend` |
| 热更新后 schema/脚本不生效 | 须 `systemctl restart`（`reload_config` 不重载 schema 缓存） |
| NFS server 地址变更 | `batch_hot_update` 不改 fstab；须逐台 remount（`2026-storage-roles-and-aliases.md` §6） |

## 10. 与相关 skill / 文档的边界

| 场景 | 用哪个 |
|---|---|
| 新 host 从 0 到 ONLINE + storage 合规 | **本 skill** |
| 已有 host 推代码 / 控制面升级 / scan | `control-plane-deploy` |
| 设备租约卡死 | `device-lease-release` |
| 跑 pytest/vitest 前环境 | `test-env-self-check` |

## 11. 校准记录

| 日期 | 校准了什么 | 来源 |
|------|-----------|------|
| 2026-08-28 | v0 创建；14 台批量接入全链路（SSH → HDD ext4 → NFS → install → env → hot-update → storage 48/48 healthy） | 生产扩容实操 |
| 2026-08-28 | 双盘 12 台 `/dev/sda` NTFS → ext4 `/mnt/hdd`；单盘 2 台对齐单盘老机 | 同上 |
| 2026-08-28 | `MOUNT_POINTS` / `STP_AEE_*` 装后手工补全；`5039` 端口误配与双 ADB 修复 | 同上 |
| 2026-08-28 | pip 清华镜像 403 → 改公网 PyPI | 同上 |
| （下次新机接入） | | |
