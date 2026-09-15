# flash 消费方脚本收敛到 wrapper 窄面：preflight v1.0.2 / firmware v1.3.16（#2133）

Status: implemented
Class: feature

## Decision

ADR-0037 D5 的消费方落地（wrapper 侧见 [2026-09-15-adr0037-d5-flash-primitives-2133](./2026-09-15-adr0037-d5-flash-primitives-2133.md)）。
两个**新版本目录**（已发布版本不可原地改）：

**`flash_preflight v1.0.2`**（参数 schema 与 v1.0.1 相同）：

- udev 缺规则 → 只经 `stp-agent-priv ensure-udev-rule`（能力探针带子命令本体，
  #2011 教训）；wrapper 缺失/旧版本 → 明确失败 + `update_agent.yml` 指引；
  修复动作受 `fix` 参数约束。
- apt：**只检不装**——缺包即失败并给出「装包归 provisioning」指引；
  `skip_apt=true` 降级 warning。`_sudo_sh` 整体删除（不再有
  `sudo -n sh -c` 任意命令面）。
- dialout 改读**持久成员**（`/etc/group` 成员表或主组），消除旧版「usermod
  后进程组不刷新 → 每次重报 fixed」的假修复（生产 36/36 次，2026-09-12 取证）；
  判定链：持久∩进程 → ok；仅持久 → ok + `pending_relogin` warning；
  仅进程 → ok + 提示；两者皆无但 udev 0666 在位 → ok + warning（访问可用）；
  全无 → 失败（运行期不再 usermod）。
- 旧 `sudo-nopasswd` 硬门移除（其语义是「免密 sudo 可信度」，会把无需修复的
  主机也整步打失败）；替换为 **warning-only** 的 `priv-face` 可观测项
  （wrapper 在位 / `ensure-udev-rule` 可用），供车队侧看迁移收敛进度。
- 检查顺序调整为 qt → flashtool → **udev → dialout**（dialout 的降级判定要读
  修复后的规则状态）。

**`flash_firmware v1.3.16`**（参数 schema 与 v1.3.15 相同）：

- 门控 `_set_authorized` 回落面由 `sudo -n sh -c 'echo … > …'` 收敛为
  `stp-agent-priv usb-authorized --port <p> --value <v>`（port 正则 +
  idVendor=0e8d 校验在 wrapper 内强制）；`_sudo_available` 删除。
- 能力探针（`usb-authorized --help`）按进程缓存；wrapper 不可用 → `(False,
  "no-priv-face")`，门控降级并在 `gating.errors` / `skipped_reason` 记录
  （非致命，与旧「无 sudo」降级语义一致）。
- 提示文案更新为 provisioning 语义（不再指向 `sudo usermod`）。

**显式不做 / 过渡语义**：新版本**不保留** `sudo -n sh -c` 兼容回落。理由：
(a) 回落面是本次要退役的对象，留着会静默腐化并掩盖 wrapper 未铺开的事实；
(b) 收敛前两台的 flash 主机将先经 `update_agent.yml` 铺新 wrapper（canary），
再由 #2134 的 C 步删宽文件——顺序可控；(c) 缺口被触发时的失败/降级都有
明确指引与记录，不会静默降级。风险面：udev 规则缺失时若 wrapper 未铺开，
preflight 会**失败**（旧版会 sudo 自愈）——这是 D5 的既定取舍，已写入 #2133。

**参数 schema 未加 seed 迁移**：两版本参数与前任完全一致，新版本行由
`scripts scan` 注册（近期版本的同款 practice，v1.3.11+ 均无 seed）；如需
UI 侧默认参数展示再行设置，不在本 PR 扩面。

## Alternatives

- **保留 `sudo -n sh -c` 作为过渡回落（哨兵上报 legacy/wrapper）**：否决——
  见上；且宽文件删除后回落只会失败，等于把「未铺开 wrapper」伪装成运行时
  偶发失败。
- **apt 装固定包清单也进 wrapper（混合·完整路线）**：否决（2026-09-15 决策
  取「混合·精简」）——装包更适合 provisioning；wrapper 面越小越好。
- **dialout 缺失直接判失败（不做 udev 降级）**：否决——比消费方宽松度更严
  （flash_firmware 的 env_precheck 对「无 dialout 但有 0666 规则」只记
  WARNING），会造成 preflight 拦住本可成功的刷机。
- **保留 `sudo-nopasswd` 硬门但改为 warning**：否决——该项的语义已被
  `priv-face` 覆盖且有更明确的窄面含义；保留会造成两个同义项。
- **seed 迁移停用 v1.0.1 / v1.3.15**：否决——两版本仍被在窗 Plan 引用
  （v1.0.1 refs=4，v1.3.15 refs=3），种子治理要求先重指 plan_step；且
  flash_firmware 1.3.14/1.3.15 双活本就是既有形态。

## Verification

- `pytest backend/agent/tests/test_flash_preflight_v102.py
  backend/agent/tests/test_flash_firmware_v1316.py -q` → 29 passed
  （跨版本常量对齐一项在 wrapper 窄面未合入前 skip，依赖 #2142）；
- `pytest backend/agent/tests/ -q`（全量 agent 回归，PR 内实跑）；
- 源码级断言：两脚本均无 `"sh", "-c"` / `_sudo_sh` / `_sudo_available`；
  wrapper 调用 argv 形态精确断言（--port/--value 成对）。

## Revisit

- #2142（wrapper 窄面）合入后：把 `test_udev_constants_match_wrapper` 的
  skip 转实断言（本 PR 的 CI 在合入队列 rebase 后会自然生效）；
- canary 真机验收（#2133）：两台 flash 主机铺新 wrapper 后，用 v1.0.2/v1.3.16
  跑一次真机刷机；再按 #2134 C 步在无宽文件状态复跑；
- 若真机出现「udev 缺失 + wrapper 缺失」导致 preflight 阻塞且不可接受：
  按 D5 方向补 provisioning 覆盖（install/update 链保证 udev），不回退
  `sudo -n sh -c`；
- 新增量若演化出参数差异，再按版本规程发新版本（不得原地改本版）。
