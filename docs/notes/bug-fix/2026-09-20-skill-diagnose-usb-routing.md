# diagnose-device-stall 补 USB 层症状路由与两条守卫（skill-only）

Status: implemented
Class: bug-fix

## Decision

只改 skill 本体（`.claude/skills/diagnose-device-stall/SKILL.md`），三处、不动权威文档：

- **触发面**：`trigger_phrases` 补「lsusb 不识别 / USB n 与在线差值异常」「主机页设备数
  为 0 但心跳新鲜」——09-20 .102 排查从该症状进入时，SOP 正文没有任何一步把它路由到
  四层判别，全靠顺链巧合命中 triage 文档；
- **步骤 2 反向守卫**：原文「双 server → kill-server 后统一 5037」教的是默认反射；
  改为 kill-server 仅当 L2 判据（`ff:42` 接口集合 ⊋ adb 列表）成立时对症——09-14
  对照实验（kill 后枚举逐台不变）与 09-20 fleet 实测（5 台低在线全为 L4、零例 L2）
  双证；
- **步骤 4 L1 处置**：设备硬件分支升级为「先四层判别的 L1 判据 → 空闲窗
  unbind/rebind 可反复救、无需 reboot」（.102/.63 于 09-20 双验证；.63 死 11 天、
  .102 死 7 天，rebind 后 SS 全树 5000M 恢复、风暴清零）。

守卫段另补两条负向约束：android 用户读 journalctl/dmesg 静默返回空＝**假阴性**
（取证须 sudo 或零权限 sysfs 判据）；「在线 ≪ USB n」的已知 L4 MIDI 存量机名单
（.20/.65/.70/.87/.66）勿动主机。

## Alternatives

- 把案例细节写进 skill 正文——否决：skill 只放触发/顺序/红线（AGENTS.md 最小方案），
  台账数据归 triage/8.87 文档；但那份补录与在飞 PR #2911 的文件（triage.md、
  incident-2026-07-29）冲突，留待其合入后随 #2902 一并做，本单刻意不碰这两个文档；
- 只加链接不加守卫——否决：步骤 2 的既有措辞本身就是会误导值班做无效动作的
  负资产，不修比不加更糟。

## Verification

- `tools/dev/check_governance_surface.py --check`：S1–S15 全绿（含 Agent Note 结构）；
- `scripts/run_gates.py check:quick` 通过（记录于 PR 评论）；
- 内容判据全部可溯源：L2/L4 判据命令与 09-14 对照实验＝triage 文档 §2/§4；
  rebind 命令＝8.87 文档 §3.1；09-20 双验证＝.102（15:28 rebind，48min 静默、
  11/11 在线）与 .63（15:23 rebind，23/23 在线）现场记录。

## Revisit

- #2911（PR）合入后，若其把「09-20 案例」写进 triage/8.87 文档，本 skill 引用的
  章节号需复核；#2902 落地 `usb_tree_empty`/接口计数上报后，步骤 2/4 的判据命令
  可改为「先看平台信号再上机」，届时再瘦一版；
- skill 的 journald 权限守卫若遇 onboarding SOP 补 `systemd-journal` 组
  （#2911 评论在议）则半过期，随该变更回看。
