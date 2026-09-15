# ADR-0037 v0.3 安全评审一稿（R02 联审 / CodeBuddy · 3e833f）

## 范围与独立性

- 任务：ADR-0037 §5 验收 ③「R02 安全联审（独立于实现，**需评审一稿**）」；交付单
  [#2206](https://github.com/DUElost/stability-test-platform/issues/2206)，上游台账
  [R02 #910](https://github.com/DUElost/stability-test-platform/issues/910)（认证、授权与安全边界）。
- Harness：CodeBuddy；会话 `01a0a2e5-9793-7488-acf6-e30c623e833f`；Execution
  `review-adr0037-security-3e833f`（role=`review`，test_impact=`none`）。
- **审查基线：`a9e2aa32e4486f42c8391a22e3a2ce99fc065b21`**（= 本次评审开始时的 `origin/main`）。
  下文源码 `file:line` 均指该提交；机队侧为**同一时段的只读实测**（命令与结果见附录）。
- 独立性边界：只读 ADR、权威文档、源码、测试与机队只读探针；**未**读取其他 ADR-0037 评审稿
  （不存在）、未改 ADR/代码/测试/issue 结论、未操作设备、未读取凭据；唯一副作用是附录中列出的
  只读命令（含 `sudo -n … selftest`，该命令实现为只 print、不落盘）。
- 评审对象是 **v0.3 文本与当前实现**；ADR 状态变更（Proposed→Accepted）不在本稿裁决范围内。

## 总评

**建议接受（附 3 项建议、3 项观察，无阻断项）。**

v0.3 的核心主张经「源码逐条核验 + 机队只读实测」双向确认：提权入口已收敛为单一 wrapper，
存量宽规则与 legacy 回退面均已退役，失败模式为 fail-closed，迁移期的两个不对称（宽文件仍存、
legacy 仍可用）都已随 #2134 / #2180 收口。建议集中在**把已经成立的控制显式化为 ADR 级不变量**
（S1/S3/O2）与**主机侧审计缺口**（S2）；三者都不影响 v0.3 转 Accepted，但建议随下一次修订落笔。

## 核验矩阵（主张 → 证据 → 结论）

| ADR 主张 | 证据（基线上） | 结论 |
|---|---|---|
| D1 单一提权入口；不授任意 `rsync/cp/chmod/chown/ln/stat` | `stp_agent_priv.py:473-494 build_sudoers_lines` 仅生成「固定服务名 systemctl 4 动作 + daemon-reload + wrapper 单命令」；机队实测 `/etc/sudoers.d/` 全队 = `README + stability-test-agent`，`sudo -n id -u` 全拒（无 `NOPASSWD: ALL`） | ✅（附 S1） |
| D2 子命令白名单 + 内部校验 | `apply-code`/`apply-resources`：`--staged` 必须在 `INSTALL_DIR` 外 + 调用者属主校验（`fstat` uid）+ `setuid(agent)` 后 rsync（`--no-owner --no-group --delete --delete-excluded --safe-links` + 固定 excludes）+ fd 基目标目录；`sync-env` 走 fd 基 `.env` 读写与 payload 形态校验；`usb-authorized`：`--value ∈ {0,1}` + `realpath` + 「`/sys/devices` 下同名真实目录」判定 + `O_NOFOLLOW`；`fix-ownership` 仅遍历 `INSTALL_DIR`、`follow_symlinks=False`；`write-digest` 校验 `sha256:<64 hex>` | ✅（附 O2） |
| D3 存量迁移；v0.3 fail-closed | 远端脚本头（`host_updater.py:232-243`）：`if ! sudo -n $PRIV selftest; then ERROR(指引) + exit; fi`，无 `USE_PRIV_WRAPPER` / `STP_PRIV_FALLBACK`；`tests/test_remote_script_privilege_paths.py` 以负向断言锁定 | ✅ |
| §4 不变量：NOPASSWD ⊆ {固定 systemctl, wrapper}；wrapper/conf root 属主且不可被 group/other 写 | 机队 48/48：`/usr/local/sbin/stp-agent-priv` = `root:root 755`、`/etc/stp-agent-priv.conf` = `root:root 644`；wrapper sha 全队一致 | ✅ |
| 验收 ①「无法对安装目录外任意路径提权写」 | `_validate_install_dir`（`realpath` 先行 + 禁止与 `/etc /usr /var …` 互相包含）+ 上游测试 `tests/test_agent_priv_boundary.py:198-232`（拒绝集 + 放行集 + 被写坏的 conf） | ✅（附 S3） |
| 验收 ②「热更新仍可用」 | 合入后主干全队 `batch_hot_update --direct`：`ok=47 converged=47 fail=0 skipped=1`（跳过项 = 有在跑任务的主机；内容为 digest no-op，恰好只验 fail-closed 预检） | ✅ |
| 验收 ③ R02 安全联审 | 本稿 | — |

## 建议（建议随修订或后续单落地，均不阻断接受）

- **S1｜把「sudoers 不约束参数」这一事实在 ADR 里显式化。**
  `build_sudoers_lines` 的 wrapper 授权行是**不带参数模式**的
  （`android ALL=(root) NOPASSWD: /usr/local/sbin/stp-agent-priv`）。按 sudoers 语义，
  这等于**允许任意参数**——因此 D1「单一入口」并没有在 sudoers 层约束参数空间，**全部 containment
  都在 wrapper 进程内**（含 `bootstrap`：`_require_root()` 在经 sudo 调用时必然通过，故该子命令对
  Agent 用户可达，依赖 `realpath` + `_validate_install_dir` + `_reject_anchor_drift` 三道守卫）。
  建议在 §2 D1/D2 与 §4 不变量处各加一句「参数空间不受 sudoers 约束，边界由 wrapper 内部校验承担」，
  并把 parser/边界测试（`test_agent_priv_parser_contract.py` / `test_agent_priv_boundary.py`）
  列为**不变量**（否则回归时可能被无声改掉而无 ADR 级约束）。
- **S2｜主机侧缺少特权调用审计（R02 范围含「命令入口和审计」）。**
  全仓 grep：wrapper 内**无任何** `logging/logger/syslog/journal/audit` 调用。平台侧记录的是
  *经 agent 发起*的步骤（step_trace / 热更新结果），而**直接**调用（如运维手工
  `sudo stp-agent-priv …`、或拿到 `android` 执行能力的进程）在主机侧**零留痕**。
  建议：每次调用落一行固定格式（UTC 时间、`SUDO_UID`/uid、子命令、关键参数摘要）到固定路径
  （root:root，logrotate）或 journald；不引入可写口、不影响 fail-closed。
  这是取证/可观测性缺口，不是可提权的漏洞——但它是本 ADR「审计」面的唯一空白。
- **S3｜宽根 `INSTALL_DIR` 的纵深防御。**
  `_INSTALL_DIR_FORBIDDEN_ROOTS`（`stp_agent_priv.py:203-206`）未含 `/opt`、`/srv`、`/tmp`
  这类共享根，`_validate_install_dir` 只要求「绝对、规范化、不与系统目录互相包含」，故
  **`/opt` 本身可通过**（上游测试放行集含 `/opt/stability-test-agent` 等两级路径，合理）。
  影响面有限：`_reject_anchor_drift` 要求 conf 存在时锚点不可变，而机队 48/48 的 conf 均在，
  故现网**不可达**；仅在「sudoers 已写、conf 缺失」的不一致态下，Agent 用户可把锚点指向 `/opt`，
  随后 `fix-ownership` 会 `chown -R` 该目录树、`apply-*` 会以 agent 身份同步进该树。
  建议二选一：① 要求 `INSTALL_DIR` 至少两级且显式拒绝共享根；② 把「sudoers 存在 ⇒ conf 必须存在」
  写成不变量并加断言（例如 `selftest` 里顺带校验 conf 存在与属主）。

## 观察（不要求行动）

- **O1｜`_reject_anchor_drift` 明确不校验 `SUDO_UID`**（docstring 说明：Ansible `become: true` 会设置
  `SUDO_UID`，加了会打断部署）。设计上可接受（信任边界是 conf 冻结而非调用者身份），但这句话目前
  只在代码 docstring 里；建议在 ADR D3/§4 写明，避免读者误以为存在调用者身份校验。
- **O2｜多条强控制未在 ADR 点名**（staged ∈ INSTALL_DIR 外 + 调用者属主、fd 基目标目录、
  `--safe-links`、`usb-authorized` 的 realpath/同名目录校验、`fix-ownership` 的 `follow_symlinks=False`）。
  建议固化进 §4 不变量清单——它们是这版实现最值得保护的部分。
- **O3｜`systemctl` 授权面含 `daemon-reload`（无参数面）**，面上很窄；若将来服务名或动作集变为
  可配置，需按「参数面可变即边界可变」重新评估。
- **O4｜wrapper 无版本标识，回滚/对账只能靠 sha 人工比对。**
  `stp_agent_priv.py` 内无 `VERSION`/`__version__` 常量，`selftest` 输出只有
  `wrapper=/conf=/install_dir=`（`stp_agent_priv.py:468`）；同时该文件被排除在部署身份
  （payload/摘要的 `FIXED_EXCLUDES`）之外 ⇒ 「这台跑的是哪版 wrapper」没有自述，只能靠
  `sha256sum` 对照 git 历史（本次评审即如此取证）。建议加一个版本常量（并在 `selftest` 里输出），
  使回滚判定与机队对账不必依赖外部 sha 表。

## 与 R02 台账的关系 / 后续

- 本稿即 §5 验收 ③ 的「评审一稿」；ADR 状态与 S1–S3 的采纳由决策者裁决（#910 台账）。
- ADR Revisit #2（ADR-0035 per-host 凭据落地后重审 wrapper 授权主体）仍是长期 open 项；
  Revisit #1（legacy 退役）已按 v0.3 执行完毕并有执行单证据。

## 附录：证据命令与结果（脱敏）

```
# 机队只读（48/48；主机地址已泛化）
ansible … android -m shell -a 'sudo -n /usr/local/sbin/stp-agent-priv selftest …'   → 48/48 wrapper（两次独立运行一致）
ansible … android -m shell -a 'sudo -n id -u …'                                     → 全部 denied（无宽面）
ansible … android -m shell -a 'sudo -n /usr/local/sbin/stp-agent-priv nosuchcmd …'  → 全部 denied（子命令面受约束）
ansible … android -m shell -a 'stat -c "%n %U:%G %a" /usr/local/sbin/stp-agent-priv /etc/stp-agent-priv.conf'
    → 48× root:root 755 / 48× root:root 644
ansible … android -m shell -a 'ls /etc/sudoers.d/ …'                                → 48× 「README stability-test-agent」

# 阴性对照（I4 演练容器，非生产；用完已复原）
移除 /etc/sudoers.d/stability-test-agent → selftest 判据翻转为 denied；cp -a 恢复 → 回到 allowed

# 热更新回归（合入后主干）
batch_hot_update.py --direct → SUMMARY ok=47 converged=47 fail=0 skipped=1（跳过=有在跑任务）
日志中 priv_mode / legacy 哨兵出现 0 次

# 测试
pytest backend/tests/services/test_host_updater.py tests/test_remote_script_privilege_paths.py -q → 35 passed
```

> 说明：本稿不引用任何凭据、连接串与完整主机地址；设备序列号与内网地址均已掩码/泛化。
