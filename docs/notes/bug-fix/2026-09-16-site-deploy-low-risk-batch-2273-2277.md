# 站点/部署链低危批：判据不问事实 + 硬化只做一半（#2273–#2277）

Status: implemented
Class: bug-fix

## Decision

五处，跨站点安装器（`tools/site_config`）、Ansible 双 playbook 与 `deploy/` 壳脚本：

**#2273 数据盘探测与 fstab 判据不问事实。**
`probe_data_disk` 取回了 `MOUNTPOINT` 却不用：**整盘文件系统且已挂载在别处**的盘
（`mkfs.ext4 /dev/sdc` 无分区表 → 无 `PKNAME` 子项、`TYPE` 仍是 `disk`、`blkid` 也返回
类型）会被当作「可用空盘」自动提案，`init --yes` 于是**二次挂载**它并把 AEE 写入压到
另一个角色正在用的文件系统上。判据补上 `MOUNTPOINT` 非空即跳过。
`_fstab_entries` 两处：文件系统类型由 `blkid -s TYPE` 取（此前硬编码 `ext4`——XFS/Btrfs
盘会写出无效条目，`nofail` 只保不挂起开机、不会让错误类型生效）；「是否已声明」由
整文件子串改为**按 fstab 第 2 字段（挂载点）**判定（注释里的同形路径不再算已声明，
`/srv/hdd` 也不会命中 `/srv/hdd2`）。

**#2274 共享路径归属守卫被前缀重叠站点 id 绕过（#2088 残留）。**
判据是「`deploy_root` 是否为**整文件子串**」：B 写下的 unit 引用 `…/stp-controlB/...`，
A 的根 `…/stp-control` 是它的真前缀 → A 把 B 的资产认作「本站」并覆盖（把 B 的服务改指
A 的部署根，正是守卫注释里写的后果）。新增 `declares_deploy_root(text, root)`：按**路径
组件边界**判（根之后不得紧跟路径组件字符），子路径（`<root>/venv/bin/python`）仍算本站。
`shared_path_is_foreign` 与 `_distro_default_conflict` 同源改用该判据。

**#2275 #2112 的 digest 硬化只做了一半（装机路径漏改、门禁只读升级路径）。**
`install_agent.yml` 仍是 `regex_search(..., '\1')` 形态（多行 stdout 下返回 list → marker
被写成 `["sha256:…"]` → agent 正则拒绝 → 上报空 → 控制面「空值不覆盖」）。改用与升级路径
相同的 `regex_findall(...) or ['']`，并补上「写入后 slurp 读回 + 断言形态与取值」对；
`tests/test_ansible_digest_bookkeeping.py` 改为对**两份 playbook** 都跑（避免同类硬化再次
只做一半）。

**#2276 `$STP_BUNDLE` 静默忽略 / 反向误拒。**
① 站点已存在时安装实际用 `site.yaml` 的 `release.bundle`，而命令行给的 `STP_BUNDLE` 从不
比对 → 打印 `reusing existing release bundle <新路径>` 却用旧路径完成一次「全绿」的旧版本
安装。改为**不一致即拒绝**（退出码 2，给出记录路径与两条出路）。
② `deploy/agent/install.sh` 对 `$STP_BUNDLE` 强存在校验 → 站点实际用别处时把健康站点判成
「不可装」。改为优先用 `site.yaml` 记录（`deploy_site_identity` 增打第三行；`deploy_defaults`
记录 `STP_BUNDLE_EXPLICIT` 以区分「显式给出」与「落到默认值」）。
`docs/operations/installation.md` §7 同步说明。

**#2277 挂住的子命令把安装崩成 traceback。**
`LocalOps.run` 补 `except subprocess.TimeoutExpired` → `CommandResult(124, …)`（124 = shell
`timeout` 的退出码，既有的「外部命令失败」检查自然接住）；并在 `install.py` 的阶段调用点
统一包 `_run_stage`：未映射异常落成 `stage_crashed` 报告条目（异常仍进日志），保证任何
异常都不穿成 traceback——操作员要的是失败码 + Fix，而不是栈。

## Alternatives

- **#2273-a 只在 `init` 交互路径提示**：否决。`--yes` 才是出事路径（无询问直接二次挂载）。
- **#2274-a 改成「必须带本站渲染标记才算本站」**：更严但会误伤——发行版资产与运维手改件
  本来就不带标记（#2088 已为此把判据定为「引用本站部署根」）。取最小修正：只把子串换成
  组件边界。
- **#2275-a 只改表达式、不补读回断言**：否决——本缺陷（列表形态）**只有回读断言能当场拦住**，
  写入任务会静默成功（#2112 的教训原文）。
- **#2276-a 以命令行覆盖并回写 site.yaml**：可行但改变站点输入的权威来源；取 fail-closed 的
  显式拒绝，让「改站点输入」是操作员的显式动作。
- **#2277-a 只在 `install.py` 包一层 `except Exception`**：否决（单做会丢掉准确退出码）。
  两者都做：`ops` 给 124，阶段层兜底契约（含第三方库异常）。

## Verification

- **红绿双向**（把 `tools/site_config/{ops,bootstrap,stages,install,checks}.py` 与 `deploy/`
  三脚本还原到 `HEAD` 后跑本批新用例）：**8 条红**（数据盘 MOUNTPOINT、两条 fstab、前缀
  重叠覆盖、stage_crashed、四条 bundle/identity）；其中 `test_site_ops_run_mapping.py`
  在旧实现下连常量都不存在（ImportError）。超时映射另做行为级差分：
  - 旧：`TimeoutExpired` **抛穿**（穿出 stage → traceback）；
  - 新：`124 | mount: timed out after 1800s`。
- #2275 的红绿：旧 `install_agent.yml` 下 `test_ansible_digest_bookkeeping.py` **3 条红**，
  改后 5 passed。
- 恢复新实现后：`pytest tests/ -k "site or deploy or ansible or inventory"` → **560 passed**。
- `ruff check backend/ tools/ scripts/` 全绿；`check:quick` → **OK (10 gates)**。
- **未跑**：真机站点安装 / 真实 Ansible 跑批（需要站点主机与凭据）；本批为判据与壳脚本改动，
  交 CI 与现场复核。
- **未做**：#2276 建议里的「`--public-url` / `--database` / `--data-disk` 重跑取值同样不
  静默丢弃」——那三者在 `init` 里本就以「站点文件存在即不消费」为语义（`init` 只生成一次），
  与 bundle 的「安装实际用记录值」不同型；未在本批扩大（见 Revisit）。

## Revisit

- **#2274 的更强判据**：组件边界解决了前缀重叠，但「同机第三站点」仍靠同一判据。若将来出现
  需要按站点隔离共享资产的场景（如同一主机多站点各自 nginx site 名），需引入站点后缀化的
  资产名，而不是继续加强文本判据。
- **#2276 的其余参数**：`--public-url` / `--database` / `--data-disk` 在站点已存在时同样
  「不消费」；是否需要像 bundle 一样比对/拒绝，取决于现场是否真按文档在重跑时传它们——
  当前无证据，留观察。
- **#2277 的 timeout 值**：`COMMAND_TIMEOUT_SECONDS=1800` 是硬编码常量；若某类命令（如大
  bundle 传输）正常就需要更久，应按命令类型分类而不是抬高全局值。
- **#2275 的读回断言只覆盖 marker 文件**：playbook 里其它「写后不验」的产物（如 unit 文件）
  未纳入；若再现「写完读回不一致」类缺陷，按同一模式补。
