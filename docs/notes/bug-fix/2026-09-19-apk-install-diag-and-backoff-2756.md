# Agent Note — 三族 APK 安装：push 失败输出保留 + 退避重试（#2756）

Status: implemented
Class: bug-fix

Issue: #2756（run 431 取证：链交接瞬时 adb 风暴 67/537 台安装失败）

## Decision

三个家族同批出新版本（ADR-0020 全量副本、基线为各自当前激活版）：

- `powercycle_setup` **v1.2.0**（基线 v1.1.0）：`install_apk` 保留 push 与
  pm install 的 stdout/stderr 进错误消息（v1.0.2 起 `rc, _, _` 丢弃 push 输出，
  连接层错误只剩 `rc=N`）；重试前 `wait-for-device` + 短退避（默认 10s，
  `STP_ATT_INSTALL_RETRY_BACKOFF_SECONDS`）。保留 #1690 PROGRESS 心跳包装。
- `sleep_setup` **v1.0.3**（基线 v1.0.2）：同模式修复（run 428 的 15 台同族）。
- `gpu_setup` **v1.2.1**（基线 v1.2.0）：`_install_apk_stable` 同修（返回
  `(rc, out)` 形态不变，push stderr 进 out；退避 env
  `STP_GPU_INSTALL_RETRY_BACKOFF_SECONDS`；保留 #2048/#1690 行为）。

证据链（09-19 00:56 取证）：rc 分布 1×43/255×14/20×5 为 adb 连接层码；同批
52 台 MTK 30 分钟前 monkey 段 51/52 全过（非渐进存储满）；67/67 台事后全部
`adb_state='device'` 自愈（非持续损坏）；NFS 资源 mtime 08-31/09-01 未变。

## Alternatives

1. 只修 powercycle——否决：三族 `rc, _, _` 同模式（sleep 428 已有同族失败、
  gpu 378MB push 风险面更大），issue 范围即含同族。
2. 大改安装通道（重试指数退避/电路 breaker）——否决：瞬时风暴窗口秒级，
  固定短退避已覆盖；复杂化设备端脚本违背自包含约束。

## Verification

- `pytest backend/agent/tests/test_powercycle_scripts.py test_sleep_scripts.py
  test_gpu_scripts.py` → 68+76 passed（新增 8 用例：push 输出保留、瞬时失败
  重试恢复、退避 env 覆盖、pm 失败输出保留）。
- `check-script-version-immutability.py --base origin/main` → 通过（无已发布
  目录原地改动）。
- 待部署后：`POST /scripts/scan`（conflicts 须为 0）+ 周期回归 Plan 52/53/54
  的 setup 步切至新版本；下一窗口 APK 失败率应回落 ~2% 基线。

## Revisit

- 若退避后仍有批量失败，升级为指数退避或把安装挪出 init 交接波次；
- #2757（disk 指标上报覆盖率 0）独立跟进——本次虽以时间形状排除存储假设，
  观测盲区仍在。
