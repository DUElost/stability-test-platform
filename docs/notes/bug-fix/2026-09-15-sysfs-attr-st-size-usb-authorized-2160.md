# usb-authorized 读 idVendor 误用 st_size：sysfs 恒 4096 → 门控全线失败（#2160）

Status: implemented
Class: bug-fix

## Decision

`stp_agent_priv.cmd_usb_authorized` 读 `idVendor` 改走**新 helper
`_read_sysfs_attr`**（`backend/agent/stp_agent_priv.py`）：只校验「常规文件 +
**实际读到的字节数**」，不再用 `fstat().st_size` 判大小；`_read_regular_at`
保持原样（它服务于普通文件，语义正确）。

**根因（#2133 canary 实测坐实）**：sysfs 属性文件的 `st_size` 恒为页大小
（真机实测 `4096`），与内容长度无关（`idVendor` 内容 `b'0e8d\n'` 5 字节）。
`_read_regular_at` 的 `st_size > limit` 判定因此恒真 → 每次调用在读 vendor
时 `input too large` 拒绝 → `usb-authorized` 正向路径**从未生效**：

```
$ sudo -n /usr/local/sbin/stp-agent-priv usb-authorized --port 1-5.1.1 --value 1
STP_AGENT_PRIV_ERROR: cannot read idVendor for 1-5.1.1: input too large   # rc=2
```

影响面：`flash_firmware v1.3.16` 的门控 8 口全 `authorize-0 failed via
wrapper`（非致命，单目标刷机仍成功——#402 实测），但多设备台架失去
「隐藏其它刷机态口」的保护（#1591/v1.3.8 的幽灵设备风险面回归）。
修复前 #2133 的 D5 验收与 #2134 的 C 步（删宽文件）均不推进。

## Alternatives

- **给 `_read_regular_at` 加 `check_size=False` 开关**：否决——该函数的
  `st_size` 判定对普通文件是正确护栏（schema/.env 读取依赖它防超大输入）；
  给 sysfs 特例开洞会让「哪里能用 size、哪里不能」变成调用方口头知识。
  独立 helper + 注释把语义固定在函数上。
- **读满 4096 再截断/去 NUL**：否决——真机 `read()` 只返回真实内容（`5` 字节），
  4096 只是 `st_size` 的元数据；按 4096 读会掩盖其它语义错误（且 NUL 处理
  纯属臆造）。
- **门控阶段吞掉错误继续（现状已非致命）**：否决——现状正是缺陷被拖到
  canary 才暴露的原因，非致命不等于可接受；修复才是收敛路径
  （ADR-0037 §5 Revisit #4：修 wrapper 发新版，不回退宽 sudoers）。

## Verification

- `pytest tests/test_agent_priv_flash_primitives.py
  tests/test_agent_priv_parser_contract.py -q` → **54 passed**；
- **语义回归用例**：patch `fstat` 复刻 sysfs（`st_size=4096`、内容 5 字节）
  → 新实现通过；**反向验证**（临时还原旧读法）该用例必红，报错与真机逐字一致
  （`cannot read idVendor for 1-2: input too large`）；
- **真实 sysfs 冒烟**：`_read_sysfs_attr` 读 `/sys/devices/system/cpu/online`
  （缺则 skip）；空文件/超长/符号链接各一条拒绝用例；
- 真机验收（合入后）：重铺 wrapper 到 .126/.82 → 复跑 canary → `gating.hidden`
  非空且 `errors={}`。

## Revisit

- 重铺 + 复跑 canary 若仍有门控失败：按错误信息定位（wrapper 内部逐级拒绝
  都有带因文本），必要时在演练机用假树复刻后再改；
- 本条与 #2133 早前「sysfs 是符号链接」属同类教训（**假树未复刻内核语义**）：
  后续给 wrapper 加 sysfs 面向的功能时，单测必须包含「st_size=4096」与
  「设备目录是符号链接」两条仿真，或直接引真实 sysfs 冒烟；
- `_read_regular_at` 若未来需要读取其它 sysfs/procfs 属性：一律改用
  `_read_sysfs_attr`，不得复用 size 判定。
