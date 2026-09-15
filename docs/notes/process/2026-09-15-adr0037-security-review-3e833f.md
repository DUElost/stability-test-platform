# ADR-0037 v0.3 安全评审一稿（R02 联审）——Agent Note（3e833f）

Status: implemented
Class: process

> 交付状态：评审一稿已交付；ADR 状态裁决（Proposed→Accepted）与 S1–S3 采纳属决策者（见 Revisit）。

## Decision

- **交付 R02 联审的「评审一稿」**（ADR-0037 §5 验收 ③ 明文要求）：对象 = ADR-0037 **v0.3** 文本 +
  基线 `a9e2aa32` 的实现 + 同时段机队只读实测。
  结论：**建议接受，无阻断项；附 3 项建议（S1–S3）+ 4 项观察（O1–O4）**。
- **证据是双通道的**：① 源码逐条核验（`build_sudoers_lines`、`_validate_install_dir`、
  `_reject_anchor_drift`、`apply-code/apply-resources/sync-env/usb-authorized/fix-ownership`）；
  ② 机队**只读**探针（48/48 `selftest`、`sudo -n` 拒绝面、`stat` 属主权限、`/etc/sudoers.d/` 构成）
  + 实验室阴性对照 + 合入后主干的全队 fail-closed 回归。
- **独立性边界**：只读 ADR/文档/源码/测试 + 只读探针；未改 ADR/代码/测试/issue 结论；未读其他
  0037 评审稿（不存在）；**不裁决** ADR 状态（Proposed→Accepted 属决策者/平台研发组）。
- **命名与防撞名**：`REVIEW_ADR0037_SECURITY_<date>_<会话后六位>.md`（§4.1 规则；本会话 `3e833f`），
  新文件不覆盖任何旧稿；交付单 [#2206](https://github.com/DUElost/stability-test-platform/issues/2206)，
  上游台账 [#910](https://github.com/DUElost/stability-test-platform/issues/910)。
- **S1–S3 的性质**：都是「已成立的控制显式化」或纵深防御/审计缺口，**不是**可提权的漏洞；
  v0.3 可先转 Accepted，采纳路径见 Revisit。

## Alternatives

- **只写一份摘要评论、不落评审稿**：不满足 ADR 明文「需评审一稿」，也无法承载 `file:line` 级证据。否决。
- **评审同时把 S1–S3 直接改成代码/ADR 修订**：越界——评审稿的价值是**独立可复核**；改动对象会让
  「第三方复核本稿」失去基线，且采纳与否属决策者。否决（改进留 Revisit/后续单）。
- **只做静态走查、不碰机队**：§4 的「不变量」与验收 ② 都带经验面（属主权限、宽松规则是否清零、
  fail-closed 是否真的不中断热更新）；不实测就只能复述 ADR 自述。否决。
- **不做阴性对照**：`selftest` 判据若恒真（例如宽面 sudoers 让一切都过），「48/48 wrapper」就是假证据；
  在 I4 容器移除窄面规则做一次翻转验证（用完复原），把判据本身也纳入评审范围。否决「省一步」。

## Verification

| 项 | 命令/方式 | 结果 |
|---|---|---|
| 相关测试 | `pytest backend/tests/services/test_host_updater.py tests/test_remote_script_privilege_paths.py -q` | **35 passed**（含「远端脚本不含 legacy 哨兵 / `USE_PRIV_WRAPPER`」负向断言） |
| 通道判据（两轮独立） | `sudo -n /usr/local/sbin/stp-agent-priv selftest`（48 台） | **48/48 wrapper** |
| 判据特异性 | 同机四连：`selftest` / 他路径 / `id -u` / 未知子命令 | allowed / denied / denied / denied |
| §4 不变量 | `stat -c '%n %U:%G %a' …`（48 台） | `stp-agent-priv` = root:root **755**；`stp-agent-priv.conf` = root:root **644** |
| 宽规则清零 | `ls /etc/sudoers.d/`（48 台） | 48/48 = `README + stability-test-agent` |
| 阴性对照（实验室） | I4 容器移除窄面规则 → 恢复 | allowed → **denied** → allowed（容器已复原） |
| 验收 ②（fail-closed 回归） | 合入后主干 `batch_hot_update.py --direct`（内容 no-op） | `ok=47 converged=47 fail=0 skipped=1`（跳过=有在跑任务）；`priv_mode`/legacy 哨兵 **0 次** |
| 一致性交叉核对 | wrapper `sha256sum`（48 台） | 全队同一 sha（与 git 历史对照取证，见 O4） |

## Revisit

- **ADR 状态裁决**：本稿只交付一稿；Proposed→Accepted 与 S1–S3 采纳由决策者拍板（#910 台账）。
- **S2（主机侧审计）落地面**：会改 `backend/agent/stp_agent_priv.py`——该文件**不在**
  `backend/agent/scripts/<name>/v<version>/` 版本目录体系内（`scripts-versioning` 的不可变约束不适用），
  但它被排除在部署身份（摘要/载荷）之外，改动需经 Ansible 通道部署，并同步 ADR/runbook。
- **O4（wrapper 版本标识）**：若采纳，机队对账方法可从「sha 对照外部表」升级为「自述版本 + selftest 输出」。
- **S3 方案取舍**：① 收紧 `INSTALL_DIR` 约束（至少两级/拒绝共享根）或 ② 加「sudoers ⇒ conf」断言；
  两者都要带测试，且需确认不影响 `/opt/stability-test-agent` 这类既有安装点。
- **长期项**：ADR Revisit #2（ADR-0035 per-host 凭据落地后重审 wrapper 授权主体）仍未到点。
- **多 Harness 汇总**：若其他 harness 也产出 0037 评审稿，按 §4.1 以各稿文件名区分，另做 synthesis，
  不以本稿替代汇总。
