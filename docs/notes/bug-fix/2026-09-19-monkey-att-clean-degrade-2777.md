# #2777 monkey_setup v2.3.8：att_clean 的 force-stop 超时降级 + am kill 兜底

Status: implemented
Class: bug-fix

## Decision

`monkey_setup` 的 `att_clean` 步骤里 `am force-stop com.tinno.autotesttool`
超时（30s）被记进 errors → 步骤失败 → **整个 init 判败，当窗 monkey 不跑**
（.59 六窗复发，#2650 G4；实测根因形态：AM 卡死、`dumpsys activity processes`
>6s 无返回）。att_clean 的语义是「清残留防叠加」（#894）——**清不掉 ≠ 不能跑
monkey**，判败是把清理步骤的地位抬到了与 setup 主目标平级。

v2.3.8（自 v2.3.7 全量副本，已发布版本不动）：

1. **force-stop 降级**：超时/失败不进 errors，改记 warnings；失败后走
   `am kill` 兜底（更轻的终止通道）；`am kill` 也失败仍 success，warnings
   可见——monkey 不依赖 AutoTestTool 已死才能跑；
2. **超时收窄 30→10s**：卡死设备上 30s×设备数会拖垮整窗 init 预算（两次
   尝试都 10s，最坏 20s 封顶）；
3. **prefs 清理维持失败语义**：#894 的叠加风险依赖 prefs 清除（running=true
   残留经 boot receiver 拉起专项服务），真失败值得红——本次降级只放宽
   force-stop，不顺手放宽。

按 ADR-0020 走新版本：全量副本 + immutability 门禁 + 版本测试
（`test_monkey_setup_v238.py`）；scan 注册（`POST /scripts/scan`，conflicts=0）
与 plan 重指须待合入部署后在控制面执行（SOP 后置步骤，脚本文件须先进服务
检出才可被扫描）。

## Alternatives

- **只把超时改小、不降级**：否决。AM 真卡死时 10s 照样超时，仍判败 init——
  缺陷核心是「清理失败被当成 setup 失败」，不是超时太长。
- **`am force-stop` 前先探测 AM 健康（dumpsys 探针）**：否决。探针自己也会
  在卡死态挂超时，多花一段预算只为决定走哪条同样会失败的通道；直接
  try force-stop → catch → am kill 的降级链更短且覆盖同面。
- **force-stop 改 `am kill-all`**：否决。kill-all 杀的是全部后台进程，半径
  远超清理目标；`am kill <pkg>` 定向。
- **顺带把 prefs 清理也降级**：否决（见 Decision 3）——那是语义放宽不是
  容错，#894 的教训不允许。

## Verification

- `python -m pytest backend/agent/tests/test_monkey_setup_v238.py
  backend/agent/tests/test_monkey_setup_v237.py -q` → 11 passed。v2.3.8 五用例：
  force-stop 挂死 → success + `am kill` 兜底 + 两次尝试均 10s + 顺序正确；
  双通道全挂 → 仍 success、warnings 可见；prefs 失败维持失败；AM 正常路径
  与旧版逐字段一致（无 warnings）；**对照锚点**：同场景 v2.3.7 判失败
  （钉住行为差异确由本版本引入）。
- 打桩口径：脚本模块 `from _adb import adb_shell` 是导入期直绑，patch 必须落
  在 `mod.adb_shell`——patch `_adb` 模块无效（首版测试踩到此坑，5 红后改对）。
- `tools/dev/check-script-version-immutability.py --base origin/main` → OK
  （无已发布版本被原地改动）。
- `python -m pytest backend/agent/tests/ -q` → **2190 passed**；
  `check_governance_surface.py --check` 全绿；`run_gates.py check:quick`
  **12 gates 全绿**。

## Revisit

- **真机验收**：.59 已重启留痕（issue 取证轮），下一窗 08:00 观察 att_clean
  是否复发；复发场景下本版本应表现为 warnings + init 通过、monkey 正常起跑。
- 合入部署后：`POST /scripts/scan`（conflicts=0）+ 引用 plan 的 precheck 重指
  v2.3.8（script-versioning SOP 后置步骤）。
- `step_clean` 里同形态的 `pm uninstall`/`rm -rf` 失败仍判败——语义不同
  （uninstall 失败会带残留跑 monkey），不动；若后续窗证据显示同类误判，
  按各自语义单议。
