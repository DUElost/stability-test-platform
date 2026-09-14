# 多站点 P1/I3：本地安装链（S0–S4）与受控首管理员引导

Status: implemented
Class: feature

## Decision

沿用 [ADR-0041](../../adr/ADR-0041-independent-site-delivery-and-management.md) 的独立站点边界，落实 [P1 设计](../../design/2026-09-multi-site-installation.md)的 I3（§4/§5/§6）；不扩张为远程 SSH 编排或远端只读预检（留后续切片）。

- **本地模式 `install`**（`tools/site_config/install.py` + `bindings.py`/`ops.py`/`stages.py`）：必须在 `control_plane.target` 声明的目标机内执行。S0 依次核对 `--confirm-site/--confirm-target`、主机名/地址证据、部署根归属（`.stp-site.json` marker）、平台（`/etc/os-release` + `uname -m`）、清单与 bundle 的 **ADR-0040 既有实现**重算的 code/resources 摘要、以及绑定读取——任一不通过即 `FAIL` 且不发生写入。
- **绑定最小契约**：`--bindings-dir` 须 0700、非 symlink、属主为当前用户；每个绑定一个 0600 `KEY=VALUE` 文件，文件名即 `_ref`；I3 消费 `database_ref`/`redis_ref`（HTTPS 加 `tls_ref`）与 `initial_admin_ref`（`USERNAME`/`PASSWORD`）。值与秘密只进子进程 env/文件，报告只出字段名与绑定名。
- **幂等与断点**：安装记录 `install-state.json`（`--state-dir` 0700、flock 互斥、原子写 0600）；重跑逐项重核实际状态（目录、账号、单元、schema、admin、占位符）而不是信任记录；env 首次生成（0600）后**不轮换密钥**；`--dry-run` 零写入。
- **S3**：数据库分类 `empty` / `managed(head/behind)` / `unmanaged` → 未接管库阻断；迁移复用既有 alembic 链（目标 venv；子进程 env 从渲染后的 `.env.backend` 组装）；迁移失败不进入 S4。首管理员由 `backend/scripts/bootstrap_admin.py` 受控创建：仅首次（已有任何 admin 即跳过并输出计数）、同名普通用户冲突返回 3 且不重置/不提权、密码只经环境、成功写 `initial_admin_created` 审计（`strict=True`）。
- **S4**：安装 nomigrate/migrate 单元与 logrotate、Nginx（`nginx -t` → `enable --now` → `reload`）、启动 nomigrate 并轮询 `/health`（`healthy` + `saq_ready` + `alembic_revision==head`）；启动前把部署树 `chown` 回服务账号。

## Alternatives

- **远程 SSH 编排**：更接近最终操作方式，但需要 SSH 传输、提权、断点与凭据面；不在本切片，避免为半成品引入长期双轨。
- **安装器建库**（`CREATE ROLE/DATABASE`）：需要超权凭据并扩大“未接管数据”风险；改由运维按绑定提供已建空库 DSN，安装器只读探测与迁移。
- **`os.path.ismount` 判定挂载**：识别不了同设备 bind 挂载（验收实测误报）；改为解析 `/proc/self/mountinfo`。
- **目标侧脚本用最小 env**：`backend/core/security.py` 导入即要求 `JWT_SECRET_KEY`；改为从渲染后的 `.env.backend` 组装子进程 env。
- **信任“上次已完成”实现断点**：与设计“恢复执行必须重新核对实际状态”冲突；保持逐项重核。

## Verification

- **仓库离线**（worktree，base=origin/main 137bbb90）：`pytest tests/test_site_install.py tests/test_site_config.py tests/test_site_config_plan.py -q` → **231 passed**（I3 13 / I1 188 / I2 30）；`ruff check backend/ tools/ scripts/` 通过；`scripts/run_gates.py check:quick` **10 gates OK**；`git diff --check` clean。I3 用例覆盖：幂等重跑（env 哈希不变、不重复迁移）、中断后从 marker 续跑、错误确认/他站 marker/平台不符、未接管库阻断、迁移失败不启动服务、脱敏（`PRIVATE_MARKER` 不入报告/状态）、摘要篡改、绑定目录与 state-dir 权限、`--dry-run` 零副作用。
- **一次性容器验收**（演练机 172.21.8.238：`systemd-nspawn` + `debootstrap` Debian 13，`--private-network`，宿主同路径 bind 作中心存储替身，bundle 与状态目录经 bind 挂载，Python 依赖走 bundle 内 wheelhouse 离线安装）：
  - `--dry-run` PASS（rc=0）；
  - 首装 PASS（rc=0）：`digest_matched`/`bindings_read`/`target_confirmed`/`deploy_root_ready`/`service_user_present`/`storage_mounted`/`dependencies_present`/`release_landed`/`venv_ready`/`env_created`/`templates_rendered`/`schema_at_head`/`admin_present`/`units_installed`/`nginx_ready`/`health_ok`；
  - 幂等重跑 PASS（rc=0）：`venv_present`/`env_reused`/`schema_at_head`/`admin_present`/`units_installed`/`nginx_ready`/`health_ok`，且 `.env.backend` 哈希前后一致（未轮换密钥）；
  - 负例：`--confirm-site wrong` → FAIL（`install_confirm`）；篡改 bundle 1 字节 → FAIL（`release_digest`）；
  - `/health` = `healthy` + `saq_ready` + `alembic_revision==head`；`users` 表存在 `admin|admin|Y`；`stability-backend-nomigrate` 与 `nginx` 均 active。跨轮次另已验证：空库 `migration_applied` 与首管理员 `admin_created`（含审计）。
- **验收中发现并修复的缺陷**：① marker 写入时机（S1 落根即写，避免中断后被误判他站占用）；② 同设备 bind 挂载判定（改用 mountinfo）；③ 目标侧脚本 env 组装（`JWT_SECRET_KEY`）；④ 部署树属主（root 复制/迁移后 `chown` 回服务账号，否则 `__pycache__` 权限拒绝导致重启循环）；⑤ 工具环境缺 psycopg（新增 `db_driver` 失败码）；⑥ S4 未确保 Nginx 启动（补 `enable --now`）。
- 环境事实（记录，不是产品行为）：容器 PG 集群落在 **5433**（debootstrap 期间按宿主网络建簇，5432 被宿主占用）；容器主机名须写 `/etc/hostname`；容器 `/tmp` 为 tmpfs。验收后容器已停止（`systemctl stop stp-i3`；重启命令：`systemd-run --unit=stp-i3 systemd-nspawn -D /var/lib/machines/stp-i3 -b --machine=stp-i3 --hostname=control-i3 --private-network --bind=/srv/stp:/srv/stp --bind=/srv/stp-bundle-i3:/srv/stp-bundle-i3 --bind=/srv/stp-i3:/srv/stp-i3`）。
- **pending（功能验收）**：远程编排、真实 NFS/HTTPS/CIFS、受管 Linux 分享、真实发布 bundle（R2）、B 现场 MS-01/02/04/06 与真机主链；安装成功不等于站点可上线。

## Revisit

- I4 接入 Host/Agent 与远端编排时复用本切片的绑定、状态与幂等语义；不得为安装器另立第二套配置解释器。
- B 的存储/OS/入口若超出本切片 profile（Ubuntu/HTTPS/CIFS/受管分享），先扩有限契约与反例测试，不关闭检查或自动降级。
- 发布端（R2）交付真实 bundle 后，把容器验收切换为真实发布物并保留 `--dry-run` 作为站点侧第一步。
