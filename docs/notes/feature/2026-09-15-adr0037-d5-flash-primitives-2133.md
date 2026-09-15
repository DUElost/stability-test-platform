# ADR-0037 D5 落地：wrapper flash 窄面（ensure-udev-rule / usb-authorized）（#2133）

Status: implemented
Class: feature

## Decision

在 `backend/agent/stp_agent_priv.py` 新增两个**固定面**子命令，把 flash 链的
运行时 root 需求从「宽 sudoers + `sudo -n sh -c`」收敛进 wrapper（ADR-0037
D5）：

- **`ensure-udev-rule`**（无参数）：写固定路径
  `/etc/udev/rules.d/98-ttyacm-mtk.rules`（内容 `KERNEL=="ttyACM*",
  ATTRS{idVendor}=="0e8d", MODE="0666"`，与已发布 flash_preflight v1.0.x 的
  常量逐字同源）并执行 `udevadm control --reload`；`udevadm trigger` 尽力而为。
  幂等：内容一致不重写（输出 `changed=0`），reload 恒执行、失败即拒绝
  （exit 2）。
- **`usb-authorized`**（`--port` + `--value {0,1}`）：写
  `/sys/bus/usb/devices/<port>/authorized`（flash_firmware 多设备门控面）。

实现口径（比 ADR 草稿更严的部分在此记录，作为契约细节）：

- 端口名取结构正则 `^[0-9]+-[0-9]+(?:\.[0-9]+)*\Z`（例 `1-1`、`1-5.3.1`）——
  **拒绝** `..`/`/`/空白/尾随换行等任何路径语义形态；用 `\Z` 而非 `$`
  （`$` 放行尾随换行）。拼出路径后另有 `is_within` 冗余防线。
- **设备目录是内核符号链接**：`/sys/bus/usb/devices/<port>` 指向
  `/sys/devices/.../<port>`（本机实测 `1-10 -> ../../../devices/pci.../usb1/1-10`）。
  因此设备目录这一跳**允许解析**，但 `realpath` 结果必须满足
  `is_kernel_usb_device_path`：位于 `/sys/devices` 之下 **且** basename 与端口名
  一致——解析到别处、同名不符、或 bus 侧同名普通目录一律拒绝。属性文件
  （`idVendor` / `authorized`）仍以 `O_NOFOLLOW` 打开、**原地单次 write**
  （sysfs 属性不可 rename）；目标须为 `idVendor=0e8d`（MediaTek）。
  （教训：首版对设备目录逐级 O_NOFOLLOW，单测用普通目录假树全绿，真机上
  内核符号链接会被 ELOOP 全拒——测试假树必须复刻符号链接布局。）
- 值域两层：argparse `choices=["0","1"]`（解析层）+ 执行层复核（跨层接线
  漂移也不放行）。
- 两个子命令进入 `_SUBCOMMAND_CONTRACT`（selftest 真实 argv 断言）——#2011
  教训：`--help` 探针抓不到「探针过、真调用挂」。

同 PR 扩展的验证面：`tests/test_agent_priv_flash_primitives.py`（46 例：
端口/值拒绝面、symlink 设备目录与 symlink authorized、udev 规则幂等/陈旧
内容重写/symlink 替换不跟随/reload 失败）、`tests/test_agent_priv_parser_contract.py`
（真实 argv 解析 + 缺参/坏值拒绝）、容器冒烟第 9 段（stub udevadm 的端到端
正反例）、`docs/DOC-MAP.md` 行同步 v0.2。

**未做**（后续 PR，见 #2133 工作项）：消费方脚本 `flash_preflight v1.0.2`
与 `flash_firmware v1.3.16` 改调本窄面；install/update 链的 dialout/包集合
归位；I4 lab 与 canary 真机验收。在此之前宽文件不删（D5 明文）。

## Alternatives

- **`--port` 只做字符集校验（照 ADR 草稿 `^[0-9.-]+$`）**：否决——`..` 通过
  字符集但构成路径语义；取结构正则后路径拼接前即可拒。
- **`authorized` 写走 `_atomic_write_at`（rename）**：不可行也不必要——sysfs
  属性跨 rename 语义未定义；原地写 + `O_NOFOLLOW` 已满足边界（symlink 目标
  不可被写穿）。
- **不校验 idVendor、任意 USB 设备可切 authorized**：否决——能力面比消费
  场景（MTK 刷机门控）宽，且可被用来干扰无关外设。
- **udev 规则内容接受参数或从模板生成**：否决——固定内容才能把面缩到
  「一条规则、一个路径」，参数面是 2011/1553 一类缺陷的温床。
- **把两个子命令做成一个 `flash-prereqs` 聚合命令**：否决——消费方是两条
  独立路径（规则修复、运行期门控），聚合会把失败面耦合（udev 装不上≠门控
  不可用）；分开的面各自可协商（#1942/#2024 的能力探针模式沿用）。

## Verification

- `python -m pytest tests/test_agent_priv_flash_primitives.py
  tests/test_agent_priv_parser_contract.py tests/test_agent_priv_boundary.py
  tests/test_agent_priv_target_paths.py tests/test_agent_priv_apply_code_protection.py
  tests/test_agent_priv_write_digest.py -q` → **102 passed**；
- `bash tools/dev/stp_agent_priv_smoke.sh`（python:3.11-slim 容器，真实子命令）
  → `ALL_OK`，含新增段：`UDEV_RULE_OK` / `UDEV_RELOAD_OK` /
  `UDEV_IDEMPOTENT_OK` / `USB_PORT_REFUSED` / `USB_VALUE_REFUSED`；
- `python scripts/run_gates.py check:quick` 与 `check:pr`（PR 内实跑，见 PR 记录）。

## Revisit

- `flash_preflight v1.0.2` / `flash_firmware v1.3.16` 落地后：在两脚本侧
  加能力协商（`--help` 存在性探针 + 失败回落语义），并把 #2133 验收
  （I4 lab 无宽文件 + canary 真机）勾掉；
- 若真机出现「wrapper 窄面不足」（例如 vendor 校验挡住合法门控场景）：
  按 ADR-0037 §5 Revisit #4 的方向修 wrapper 发新版，不回退宽 sudoers；
- 宽文件清除（#2134）后，本窄面即成为 flash 链唯一的 root 通道——届时
  复查 `usb-authorized` 的调用面是否与门控语义仍一致（`_restore_gated` 兜底）。
