# 多站点 P1：#2181 中心存储闭环（站点自建 NFS 导出 + Agent 侧挂载与断言）

Status: implemented
Class: feature

## Decision

把「站点自建中心存储」从**配置项**做成**闭环能力**：控制面导出、Agent 挂上并上报、
S5 断言、S6 受控写读探针。现场（城市 B 站点）实测的缺口是这张网的前两段都没接线：
4 台 Agent 上 AEE 本地根与共享挂载点**根本不存在**，Agent 报
`aee_local_root_unusable` 回退 SSD，中心存储永远是空的；而 `/storage` 页因为
`MOUNT_POINTS` 从未被设置，连「未挂载」都显示不出来（只会显示「未上报」）。

- **模型**（`tools/site_config/models.py`）：`storage.export_to_agents`（默认 `false`），
  仅 `local_mount` 可开——远端分享/受管存储由对方导出，站点再导出会形成两套来源
  （`storage_export_conflict`）。
- **控制面侧**（`stages.py`）：
  - S1 装 `nfs-kernel-server`，并把导出根交给约定写入身份（`chown root:1000` + `chmod 0775`，
    **只动这一层**，不递归既有数据）；NFS 选项 `all_squash,anonuid/anongid=1000` 与
    Agent 默认运行账号（uid 1000）对齐；
  - S2 写 `etc/exports.d/stp-<site>.exports`（客户端 = 声明 Agent 的 `/24`；目标写主机名时
    推不出网段，放宽为 `*`）→ `exportfs -ra` → `enable --now nfs-server`；首装无 Agent 时
    导出文件为空，报告如实标 `export_deferred`；`--dry-run` 标 `export_planned`，不假装已发布。
- **Agent 侧**（`backend/agent/install_agent.sh` + `tools/ansible/playbooks/install_agent.yml`）：
  - 步骤 0 声明 `nfs-common` 依赖（与 curl 同一教训：最小安装不带 `mount.nfs`）；
  - 新增 7.4 段建 AEE 本地根（归 Agent 账号、0750）与共享挂载点（只建空目录，
    **不 chown**——挂载点属主是存储端 S1 的约定，从 Agent 侧 chown 会把改动写进分享）；
  - playbook 在安装脚本**之后**挂载（挂载点与运行账号都由脚本创建），只在真的挂上时
    才写 fstab（`nofail,_netdev`）——给从未连通的分享留一条永远失败的开机挂载是负资产；
  - `MOUNT_POINTS` 跟随中心存储路径（既有空值时填充，多挂载点站点自己写的值不覆盖）：
    热更新把该键列为 `PROTECTED`，安装链是唯一能一次对齐的入口。
- **S5 断言**（`agents.py`）：新增 `install.s5.storage`——声明导出的站点，Agent 心跳
  `mount_status[storage.mount_path].ok` 必须为真（`shared_storage_mounted`），否则 FAIL
  `shared_storage_not_mounted` 并在 message 里列出实际上报的路径；未开导出的站点记
  `BLOCKED export_not_enabled`，不误报失败也不假装验过。
- **S6 探针**（`verify.py`）：`--storage-probe-subdir <name>` 显式授权后在
  `<mount_path>/<name>/` 写一个探针文件、原样读回、只删自己那一个文件；路径不是挂载点即
  FAIL（**不允许写进本机同名目录冒充共享存储**），未授权记 `BLOCKED`，子目录取值拒绝
  斜杠/穿越。

## Alternatives

- **在 Agent 上仍由运维手工挂载**（现状）：现场已经证明会漏——4 台 Agent 全都没挂点，
  而安装报告与页面都看不出来。改为安装链负责 + 断言收口。
- **用后端 env 键（如 `STP_AEE_EXPORT_TO_AGENTS`）把「本站是否导出」传给 Agent 安装服务**：
  能精确区分外部分享站点，但要动 `MANAGED_ENV_KEYS` 的缺失即冲突语义（既有站点会因缺键
  `install_conflict` 阻断）。本切片改用**窄口**：只有真的挂上才写 fstab，误判的代价从
  「留下错误开机挂载」降为「一次失败挂载尝试 + 一条提示」。
- **S5 断言改成「任一上报项 ok」**（对齐 `/storage` 页现有的宽容统计）：页面需要宽容
  （各 Agent 的 `MOUNT_POINTS` 字符串本来就可能不同），但**验收**必须精确到站点声明的
  那个路径，否则挂错路径也算通过。
- **把导出根递归 chown 给 Agent 账号**：会改既有数据的属主（S1 明令禁止全目录清理/递归改动）；
  改为只放开导出根一层 + `all_squash` 身份映射。
- **未授权也跑写读探针**（例如写进 `mount_path` 根）：会在别人的分享里留文件；
  保持显式授权 + 只删自己那个文件。

## Verification

- **仓库离线**（worktree，base=origin/main `5845bfd3`）：
  - `tests/test_site_agents.py` **78 passed**（新增 `TestStorageAssertion` 4 条：上报即 PASS、
    未上报 FAIL 且失败即停、上报别的路径不接受、未开导出 BLOCKED；`TestVerifyS6` 新增 5 条：
    授权探针写读自清、未授权 BLOCKED、拒绝穿越取值、非挂载点不写本机目录、不可写分享 FAIL；
    CLI 用例补 `--storage-probe-subdir` 透传）——并对 S5 断言做了**变异反查**：把判据改成
    恒真后，两条 FAIL 用例如期红。
  - `tests/test_site_install.py` **37 passed**（新增 7 条：客户端范围按目标推导（`/24` 与 `*`）、
    发布导出文件+`exportfs -ra`+`nfs-server`、缺包则装、外部分享站点一个字节都不写、
    `exportfs` 失败即 FAIL、首装无 Agent `export_deferred`、dry-run `export_planned` 不写盘）。
  - `tests/test_install_agent_noninteractive.py` **25 passed**（新增 8 条：`MOUNT_POINTS` 跟随
    中心存储/空值不覆盖/自定义值保留、7.4 段建目录、未配置不建、**从 Agent 侧绝不 chown 挂载点**、
    playbook 挂载在安装脚本之后且服务重启之前、fstab 只对真挂上的写、挂载需要声明服务端）。
  - `tests/ -k "ansible or agent or install or site or verify or deploy"` **652 passed**；
    `ruff check tools/` 通过。
- **pending（现场，任务 #40）**：城市 B 站点重跑 `install`（导出）+ `install --through-agents`
  （Agent 挂载与 `MOUNT_POINTS`）+ `verify --storage-probe-subdir`，核对 `/storage` 页从
  「未上报」转为已挂载；真机 B 的重装需一并复核 `nfs-common` 自动安装。

## Revisit

- **外部分享站点的 Agent 侧挂载仍是空白**：`existing_share` / `managed_linux` 站点由对方
  导出，本站不代挂，S5 记 `BLOCKED`。若要覆盖，应带上 `storage.target/protocol/share` 走
  各自的挂载协议（本切片不做）。
- **导出客户端范围按 `/24` 收敛**：跨网段或 NAT 后的 Agent 需要按实际拓扑收窄/放宽；
  主机名目标当前放宽为 `*`，站点若不能接受应改用 IP 声明。
- **挂载失败不阻断 Agent 安装**：Agent 用本地落点仍可运行，失败由 S5 断言暴露。若现场
  要求「挂不上就不许装」，应把该断言前移为安装前置条件。
- **`MOUNT_POINTS` 只在安装链对齐**：热更新按设计不碰它，存量 Agent 需要一次重装才能上报；
  后续若要免重装，应给一个受控的 reload 路径而不是放宽 protected 语义。
