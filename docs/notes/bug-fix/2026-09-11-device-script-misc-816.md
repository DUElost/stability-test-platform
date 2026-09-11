# 设备脚本杂项批 6 处修复（#816）——install_apk/oobe_skip/connect_wifi/aee_signal_trigger/aee_prepare/flash_firmware 新版本

Status: implemented
Class: bug-fix

## Decision

本质问题（#816，C×6）：六个独立设备脚本缺陷。修复 = **各发新版本**（版本目录
不可变）：

1. **install_apk → v1.0.2**：已装版本判定改 `versionName=` **字段级精确比较**
   ——子串匹配下 required=1.0.1 命中已装 1.0.10，误判 skipped（skip_reason 本是
   `==` 语义），所需 apk 永不安装；
2. **oobe_skip → v1.1.1**：adb root 后固定 `sleep(2)` 改**轮询
   `get-state==device`**（上限 15s，超时继续由命令层 rc/verify 兜底）——adbd
   重启需 2–5s+，固定窗口落在 offline 时整步失败率偏高；
3. **connect_wifi → v1.0.1**：SSID/密码经 `shlex.quote` 转义（原 f-string 拼接
   在引号/美元符/反引号下被设备 shell 拆坏）；`rc + stdout/stderr` 联合判定；
   连接后**回读 wifi status 复验**（10s 窗口）才报成功；
4. **aee_signal_trigger → v1.0.1**：kill 前 `kill -0` **存活证据** + kill rc
   判定（失败立即暴露，不再盲等轮询窗口）；新 db_history 行做 **pkg_name
   归属核对**后才认领——并发来源的 AEE 事件不再被当成本次 kill 产物（全窗口
   只有不匹配新行时明确失败并提示）；
5. **aee_prepare → v1.0.1**：开发者设置**恢复移入 finally**（失败/异常/早退
   路径不再遗留 `development_settings_enabled=1`）；adb root 后轮询
   `get-state==device` 再 setprop（上限 15s，超时由 rc+回读核验兜底）；
6. **flash_firmware → v1.3.12**：host lock 打开改
   `os.open(O_WRONLY|O_CREAT|O_NOFOLLOW, 0o600)`（`os.fdopen` 保持 file-object
   接口）——低权限用户预置 symlink 时明确 RuntimeError 拒绝，不再静默跟随
   截断 agent 可写文件（对齐 flash_preflight v1.0.1 用法）。

## Alternatives

- **原地修改各版本目录**——禁止（版本目录不可变硬不变量）；
- **connect_wifi 仅修转义不回读复验**——放弃：命令未报错 ≠ 已连接（错误落
  stderr 时原实现判不出）；复验是"成功"的必要条件（issue 要求）；
- **aee_signal_trigger 无匹配新行时回退认领**——放弃：正是要拦截的误吸形态；
  明确失败 + `unmatched_new_lines` 计数让运维可见并发来源；
- **flash_firmware 用 `open(...) + os.set_inheritable` 等替代**——放弃：
  O_NOFOLLOW 是打开语义（拒绝 symlink），set_inheritable 与此无关；
- **aee_prepare 恢复失败时上抛**——放弃：恢复在 finally，失败会覆盖主异常；
  静默保留（metrics 已反映主流程失败）。

## Verification

实际运行（worktree `/tmp/stp-816`，基于 `origin/main`）：

- `pytest backend/agent/tests/test_device_script_misc_fixes.py -v` → **12 passed**
  （6 组：near-version 不 skip / exact skip、adbd 轮询成功与超时、凭据转义+
  复验、rc 失败、缺归属拒绝、其它包行忽略、kill 失败、finally 恢复、symlink
  拒绝、源码 O_NOFOLLOW 防回退）；
- **反向验证**：测试指向各旧版本 → **11 failed / 1 passed**（唯一通过者为
  exact-match skip——两版行为一致，合理）；恢复后 12 passed；
- `check:quick` → 7 gates 全绿。

未完成（pending）：

- 真机侧（6 处各自的环境验证：真机 root 重启窗口 / WiFi 特殊字符 SSID /
  并发 AEE 事件 / symlink 预置）——需真机或隔离环境；行为由单测覆盖。

## Revisit

- 若 `dumpsys` 版本行格式随 Android 版本变化，install_apk 的字段解析需同步；
- connect_wifi 的回读窗口 10s 在慢设备上可能偏紧，可按实测调整；
- flash_firmware 的失败 token 裸子串问题（`FAIL`/`ERROR` 误判整轮、`text=True`
  非 UTF-8 解码）为 issue 中"疑似未登记"项——本次未处理，建议另行评估。
