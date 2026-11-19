# Hub 分支「半枚举」形态在 `.60`/`.42` 复现：§5.4 补观测口径，守卫并入现役 skill 而非新建

Status: implemented
Class: process

## Decision

1. 在 [`docs/operations/host-device-visibility-triage.md`](../../operations/host-device-visibility-triage.md)
   §5.4 补两条观测口径：主机未安装 `uhubctl` 时，执行者不得把「`uhubctl` 不慢」当成
   「没有本节形态」的反证，判据与验收改走零权限观测面（sysfs 驱动链接、`lsusb -t` 的
   `Driver=[none]/0p`、`lsusb` 设备总数、`adb devices` 串号集合）；`read-kernel-log` 输出无时间戳
   （`journalctl -k -o cat`），定位「噪声何时停止」必须用 `--since-epoch` 窗口夹逼。
2. 新增 §5.6 记录 2026-11-19 两台主机的处置与验收（`.60` 有 `uhubctl` 前后计时与 reason 翻转时刻；
   `.42` 无 `uhubctl`，验收全靠零权限面），并把「三例同形 ⇒ 同一批 Hub 的 SS 侧退化」写明，
   终态出口仍指向 §5.2 第 6 条。
3. 不新建独立 skill。触发面、一条转入口径与三条负向守卫并入**现役**的
   [`.claude/skills/diagnose-device-stall/SKILL.md`](../../../.claude/skills/diagnose-device-stall/SKILL.md)：
   `description` 补「主机持续 `usb_link_degraded`、`uhubctl` 卡住或耗时数十秒、需要摘除 Hub 残留上行分支」；
   第 8 步补「L1 主控判据不成立但 reason 持续 ⇒ 转 §5.1」的转入口；「踩坑守卫」补三条
   （`Failed to suspend device` 由 Hub 打印、无 `uhubctl` 时不得用「不慢」当反证、
   无时间戳只能窗口夹逼且 `--lines` 上限 5000）。
4. §6 相关补本记录的链接。

## Alternatives

- 新建 `.claude/skills/usb-hub-branch-triage/`（本轮原先计划的写法）：放弃。
  `tools/dev/skill_usage_report.py` 的立论是「建 skill 必须被用，不许造完就闲置」，persistent 型
  「存在 ≥14 天且 Claude 侧零调用」即判 HOLLOW；新建 skill 要等一个观察窗才暴露问题，属于给自己
  埋一条将来要裁决的空壳。更直接的理由是：本场景与 `diagnose-device-stall` 第 6–8 步共用同一条
  分层入口（Hosts 页「在线 vs USB」差值、§1 L1、§2 只读采集），另立 skill 会把一条判分路径劈成
  两个入口，制造「先选 skill 再选判据」的新失误面。
  **本条的两处事实修正（2026-11-19 实测 `--strict` 后补）**：
  ① 不能写成「新建 skill 会让 CI 变红」——`skill_usage_report.py --strict` 在 **main 上就已 exit=1**
  （6 个既有 HOLLOW），它属 `check:gov` 本地画像，不在 required checks（`lint`/`CodeQL`/
  `pr-typecheck`/`pr-compileall`/`pr-agent-tests`/`pr-migrate-empty-db`）之内。
  ② 也不能写成「并入一个被实际使用的 skill」——`diagnose-device-stall` 自己就在 HOLLOW 名单里
  （Claude 侧 0 次调用；Codex 侧 3 会话读取、最近 2026-11-19，即本轮排障）。
  该报告对 HOLLOW 给出处置是「**删除或修 description 触发词**，勿留空壳」：本次改动正是为它补上
  缺失的症状触发词（持续 `usb_link_degraded`、`uhubctl` 卡住/数十秒、摘除 Hub 残留上行分支），
  方向与报告的处方一致；但**这不等于修好了 HOLLOW**（强信号是 Claude 侧调用，仍为 0），
  留待 `gov-skills` 的下一次裁决，本 Note 不声称已解决。
- 只加 §5.6 实例、不动 skill：放弃。排障者从「主机持续 `usb_link_degraded`」「`uhubctl` 卡了半分钟」
  这两句话进不来现有触发词，而 2026-11-12 那次 `.75` 误判正是「知道有文档、但从症状找不到入口」
  的产物。
- 把「摘除后等满 1 小时再复核 reason」写成硬性等待步骤：放弃。改为写明推算依据
  （`contracts/kernel_usb_faults.py` 的 `LINK_WINDOW_SECONDS=3600` + 窗口夹逼测得的停止时刻），
  两台实测分别在 57 分钟内翻转，硬等会让执行者误以为存在固定延迟。
- 把三台 Hub 的更换排期写进本节文档：放弃。排期是 Owner 与现场的决策，文档只保留 §5.2 第 6 条
  这一处出口，避免文档替人做承诺。

## Verification

- `.60`（14:49:55 `remove`，rc=0，写入阻塞 19.5s）：`uhubctl` 全量 21.8s → **0.9s/0.6s**；
  单口定向 `-l 1-4.4 -p 1 -a status -d 1` 27.3s → **0.3s**（拆除前定向比全量还慢，正是
  「耗时与端口数无关」的直接证据）；`lsusb` 25 → 20；13 台设备串号逐条一致、`adb` 在线 13→13；
  窗口夹逼 3/5/10/15/20 分钟 `-110` = **0/0/19/44/68** ⇒ 停止时刻 = 拆除时刻；
  `usb_link_degraded` 于 **15:47:02** 消失、主机转 HEALTHY。
- `.42`（17:28:40 `remove`，rc=0，阻塞 15.9s，**该机无 `uhubctl`**）：`lsusb` 29 → 24；
  bus 2 仅剩 `2-0:1.0`；**17 条 `adb` 串号逐条一致**；夹逼（17:33:32）2/5/10 分钟 = **0/1/26**
  ⇒ 停止时刻 17:28:55。该机 reason 翻转由 `LINK_WINDOW_SECONDS` 推算约 18:29，
  **撰写本 PR 时尚未到位，留作 PR 评论复核项**（不写成已完成）。
- 文档结构自检：§5.1–§5.6 顺序正确、`## 6. 相关` 的 5 条链接完整、文件末尾无悬空标题。
  **本次编辑出现过两次自伤并已修**：第一次把 §5.6 插进了 `## 6. 相关` 标题之下；
  第二次重排时把 §6 的列表推到标题之前、留下空标题。两次都由结构自检抓出并修复，
  记在这里以免被当成「一次改对」。
- **未做**：`.42`/`.60` 的 Hub 更换排期；fleet 其余 74 台的同形普查；`[none]` 计数信号化。

## Revisit

- 若出现第 4 台同形主机，即证明是批次性硬件退化：执行者应提议把「SS 分支半枚举」
  （`Driver=[none]/0p` 与 `hub_ext_port_status failed` 计数）做成 Agent 侧信号，
  并沿用 2026-11-12 记录的分桶前提——分桶不得削弱 `usb_link_degraded` 对真故障的灵敏度，
  因为本轮已证明「autosuspend `-110`」本身就可能是 Hub。
- 若 `uhubctl` 后续版本提供按 bus / VID:PID 过滤（v2.6.0-1 实测无 `-b`、无 `-D`），§5.4 应退化为
  CLI 级规避，不产生 write-once 副作用；届时同步改本节与 `diagnose-device-stall` 的守卫条目。
- 若 `gov-skills` 的 HOLLOW 判据改为承认 Codex 侧使用信号，再评估把本场景拆成独立 skill。
- 若 `diagnose-device-stall` 的 HOLLOW 判定在下一轮被裁为「删除」，本条转入口径必须同步搬进
  triage 文档 §5 的执行段，否则 §5.4 的观测口径会失去唯一的人类入口。
- `.42` 的 reason 翻转与 §5.6 的该条验收句需在复核后确认；若未按 `LINK_WINDOW_SECONDS` 推算翻转，
  说明该机内核日志里仍有其他 `-110` 源，需按 §5.1 重新分层。
