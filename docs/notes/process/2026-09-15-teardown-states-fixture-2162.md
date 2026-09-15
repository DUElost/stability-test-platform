# 设备端清理三态验证夹具（#2162）

Status: implemented
Class: process

## Decision

- **把一次性验证脚本固化成夹具**：2026-09-15 的 #894 / #2146 真机三态验证是用临时 host 侧
  脚本跑的（跑完即删）。teardown 家族（`monkey_teardown` / `gpu_finish` /
  `powercycle_finish` / `sleep_finish`）每次发版都要验三态，临时脚本不可复用 →
  落成 `tools/dev/teardown_cleanup_states.py`，用规格表描述每个脚本的入口形态与目标清单。
- **夹具自带两条纪律（都来自踩坑，写进代码而非注释）**：
  1. **构造必须消除竞态**：② 态用设备端**无间隔紧凑循环**重建目标。首轮用
     `while :; do touch f; sleep 0.05; done` 时探测正好撞上重建空档，产生**假阴性**
     （当时 A2/B2 各失败一次）——夹具现在先做**前置自检**（循环存活 + 删除后确实被重建），
     自检不过报**夹具错误（exit 2）**，绝不记成用例失败。
  2. **③ 态用真实 adb**：把探测那一跳指向不存在的 serial（`NOSUCHSERIAL0`），得到 adb 的
     真实 rc≠0；不 stub 探测输出——stub 只能验证测试自己的想象。
- **两种入口形态统一抽象**：`main` 入口（monkey 走完整脚本、`output_result` 捕获）与
  函数入口（gpu 走 `_cleanup_device_script()`、异常即失败）；探测替换对两种形态分别
  作用在 `adb_shell_quiet` 与 `_lib.adb` 上（与被测实现真实的调用面一致）。
- **安全闸默认开启**：设备上有 monkey/aim 相关进程时拒跑（`--force` 越过）——夹具做的是
  **破坏性删除**，不能在跑着测试的设备上顺手执行。
- **离线单测含一条负向对照**：把「探测丢 rc」的缺陷形态（即 gpu_finish v1.0.4 的静默假绿）
  喂给夹具，断言 ③ 态转 FAIL——保证夹具不是空网。

## Alternatives

- **只把临时脚本存进 docs/ 或 /tmp**：不可执行、无规格、下次仍会重写。否决。
- **把三态塞进 `backend/agent/tests/`（pytest）**：三态需要真实设备与 adb，CI 无设备；
  塞进去只能 skip，等于没有防线。改为一键夹具 + 文档写明跑法。否决。
- **③ 态用 `adb kill-server` / 拔设备构造**：会影响同宿主机上的其他设备与在跑测试
  （adb server 是 per-host 的）。改用「真实 adb + 不存在 serial」——rc 语义等价、影响面为零。
- **② 态用 `chattr +i` 造不可删文件**：设备多为非 root（实测 uid=2000），`chattr` 需 root；
  且那会先命中「rm rc≠0」分支，验不到「rm 成功但仍有残留」这条。紧凑循环才是对应用例。否决。
- **自动推断脚本入口/目标清单**：不同脚本的清理实现形态各异，推断会写出脆弱的启发式；
  规格表显式登记（新增脚本时补一行），可读且可审计。否决。

## Verification

| 项 | 命令 | 结果 |
|---|---|---|
| 夹具单测（离线 fake 设备 + fake 被测模块） | `./scripts/run_pytest.sh tests/test_teardown_cleanup_states.py -q` | **11 passed** |
| 负向对照 | 同文件 `test_fixture_catches_rc_losing_implementation` | 喂「探测丢 rc」缺陷形态 → ③ 态判 **FAIL**（夹具有效） |
| 真机 · monkey_teardown v1.0.2 | `--serial A2WENX66****0033 --script monkey_teardown` | **3/3 passed**（连跑 2 次稳定） |
| 真机 · gpu_finish v1.0.5 | `--serial A2WENX66****0033 --script gpu_finish` | **3/3 passed**（连跑 2 次稳定） |
| 真机结果样例 | ② 残留转红 | `cleanup 后仍存在: /sdcard/blacklist.txt`（`removed=8 / remaining=1`） |
| 真机结果样例 | ③ 探测不可用转红 | `cleanup verify rc=1（无法确认删除结果）` / `RuntimeError: 清理验证不可用：rc=1…（adb: device 'NOSUCHSERIAL0' not found）` |
| 仓库离线子集（PR 路径） | `./scripts/run_pytest.sh tests/ -q --ignore=…` | **932 passed** |
| 门禁 | `.venv/bin/python scripts/run_gates.py check:quick` | `OK (10 gates)` |
| 现场复位 | 设备 `pgrep -f 'while :; do touc[h]'` | **0** 个残留循环；宿主机 `/tmp` 夹具与日志已清 |

## Revisit

- **规格表需随新脚本补行**：`SCRIPT_SPECS` 目前登记 `monkey_teardown` / `gpu_finish`；
  `powercycle_finish` / `sleep_finish` 的清理是「prefs 回读 + force-stop」形态（失败即 raise，
  语义上天然覆盖三态），如需纳入，补规格时注意它们的目标不是文件而是 prefs/进程。
- **夹具依赖设备侧 shell 细节**：紧凑循环用 `nohup sh -c 'while :; do touch …'`，`pgrep` 用
  bracket 技巧防自匹配；不同 OEM 的 toybox 行为若变化需复核这两处。
- **未纳入 CI**：真机夹具按设计不进 CI（无设备）；建议在 teardown 家族发版时把
  「跑一次夹具」写进部署检查单（当前靠 Agent Note 记录）。
