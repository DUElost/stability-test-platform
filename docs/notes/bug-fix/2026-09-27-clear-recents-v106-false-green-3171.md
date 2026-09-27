# clear_recents v1.0.6：三处「假绿」收口——keyevent rc 丢弃 / already_clear 无正面证据 / 快照空 desc 计数归零（#3171 / B1-G5）

Status: implemented
Class: bug-fix

关联：[#3171](https://github.com/DUElost/stability-test-platform/issues/3171)（本单）、
[#3463](https://github.com/DUElost/stability-test-platform/issues/3463)（批次 B1 规划方案 §3 G5 / §4 G5）、
#2976/#3104（同文件前序版本链：dump 读取点已修，本版收按键注入与判据自洽面）、
gpu_setup v1.2.3（F2 参照实现）。

## Decision

按 #3463 §3 G5 执行（层级 = 形态级，同形态扫描见 §1 F5/F2 行，非止血），逐项落地：

1. **F5③（keyevent rc 表面化）**：`_open_overview()` 改走 `adb_shell_quiet`，rc≠0
   返回携带 rc 与 stderr 的失败原因；main 三处调用点（循环首、swipe 复核位、tap
   复核位）接既有 `_retry_read` 瞬时重试机制——消耗尝试、用尽转红，绝不落绿。
   走 `_retry_read` 而非立即红，与 v1.0.4（#3104）确立的「瞬时失败由脚本自担重试」
   家族语义一致（设备重启窗正是本脚本的目标故障形态）。
2. **F5①（already_clear 正面证据）**：新增 `_has_overview_evidence(xml, raw)`——
   `raw 任务卡 > 0` 或概览容器 id（`recents|overview|task_view`）在场，任一即可；
   不满足时按瞬时读失败消耗尝试（`read_errors` 留证据），不判 `already_clear`。
   原 `_already_clear()`（≡ `app==0`，与分支前置条件重复）被该判据取代，随分支
   重构一并移除。判据取自 #3171 修复规格「概览容器 id 在场，`raw_before > 0` 即
   够用」；「launcher package 在场」读作容器 id 所属包不限于 launcher/SystemUI，
   不单凭 package 名——桌面 dump 同样是 launcher package，单凭它挡不住「按键被
   吞停在桌面」的假绿（§4 验收项要求桌面 dump 判非 already_clear）。
3. **F5②（快照计数回落）**：`_count_app_tasks()` 快照路径信任判据从
   `if snaps:` 收紧为 `if snaps and any(desc 非空)`——desc 全空（AOSP/Launcher3
   形态）时回落节点计数，有卡屏不再数出 0。
4. **F2（宽松解码）**：`_adb.py` 的 `adb_shell`/`adb_shell_quiet` 去 `text=True`，
   显式收字节经新增 `decode_device_output()`（bytes + `errors="replace"`，port 自
   gpu_setup v1.2.3 #3069）。超时异常面不变（仍由 `_dump_ui` 的 except 兜）；
   `adb_shell_quiet` 返回重建的 `CompletedProcess`，调用方 `.returncode/.stdout/
   .stderr` 契约不变。
5. 版本：族树改动经 `check_script_packages.py --register clear_recents 1.0.6`
   登记（1.0.6 未被占用，未顺延）；manifest 纯 append。未动 `adb_push`/
   `adb_install`（非 #3171 指名的 dump/keyevent 调用点，本族流程不使用，属
   §6「明确不做」的顺手重构范畴）。

## Alternatives

- **keyevent 失败立即红（不走重试）**：与 #3104 的设计裁决相反——一次瞬时抖动即
  终结整步，`max_attempts` 形同虚设；且 acceptance 只要求「rc≠0 判失败」，重试
  耗尽后仍红，满足判据且保留瞬时自愈面。
- **launcher package 在场即证据**：能挡锁屏（keyguard 属 systemui），挡不住桌面
  （同为 launcher package）——与 §4「桌面 dump 判非 already_clear」直接矛盾，
  弃。桌面/锁屏区分交给「概览容器 id + raw 卡」两个证据源。
- ** rc≠0 时整步红 + 新增独立 metric**：新观测面超出本单规格（§6 禁顺手加面）；
  `read_errors` 已完整承载原因与 rc 证据。
- **顺带给 `input tap`/`input swipe`/HOME keyevent 做 rc 检查**：不在 #3171 指名
  调用点内，§6 明确不做。

## Verification

全部在 worktree `/tmp/stp-b1-g5`（基线 c2e11248，merge origin/main@fffb0159 追平
G2 #3464 后）实际运行：

- `python -m pytest backend/agent/tests/ -q -k clear_recents` → **27 passed**
  （新增 `test_clear_recents_v106.py` 12 例 + 存量 v105 15 例）；
- **反例有效性**：把 v106 测试文件拷入改动前基线 worktree 实跑 → 9 例失败
  （锁屏/桌面假绿 ×2、证据矩阵、desc 回落、AOSP 卡上滑、keyevent rc 单元+端到端、
  解码 ×2），3 例正向控制在两版皆绿（空概览容器仍绿、ZTE 残留主屏幕卡仍绿、
  快照 desc 可用时路径不变）——缺陷钉住面与语义保持面分离干净；
- `python tools/dev/check_script_packages.py` → OK（35 族树与最新登记等价）；
- `python tools/dev/check_tool_manifest.py --base origin/main` → OK（40 族 /
  223 条目，append-only；首次跑红为 G2 合入后本分支基线落后所致，merge 追平转绿）；
- `python scripts/run_gates.py check:quick` → OK（16 gates；worktree 需软链主树
  `frontend/node_modules`，本机环境补齐非仓库变更）。

**返修（CI `pr-agent-tests`，#3463 规划者修订 v1.1 指认）**：`test_clear_recents_v106.py`
的 `_run` 把 `time.sleep` 打成 no-op 未推进时钟，触发
`tests/test_agent_clock_stub_guard_3202.py` 两条（#3202 忙等形态守卫）。按指认改用
`_patch_advancing_clock(monkeypatch, mod)`（同构自 `test_powercycle_scripts.py`，
测试文件内自带）。修后实测：

- `python -m pytest tests/test_agent_clock_stub_guard_3202.py -q` → **4 passed**；
- `python -m pytest backend/agent/tests/ -q -k clear_recents` → **27 passed**（行为面不变）；
- `python -m pytest tests/ -q`（v1.1 §4 新增通用门禁）→ **1928 passed / 18 skipped**；
- `python scripts/run_gates.py check:quick` → OK（16 gates）。

## Revisit

- **生效链未走（Owner 统一安排）**：本 PR 只落「源码树 + 登记 + 测试」；部署、
  `--publish` 发包、scan、plan_step 重指整批一次（#3463 §5）。重指前生产仍跑
  1.0.5，三处假绿仍在。
- **空概览且无容器 id 的机型**：若某 OEM 空概览 dump 连容器 id 都没有（仅
  raw>0 可判），真「无任务」会转红而非绿——按「宁可红不假绿」裁决可接受；
  实机放量后若误伤面大，证据源可在 `_OVERVIEW_EVIDENCE_RE` 单点扩充。
- **其余约 20 族的 F2 text=True**：登记为证据（#3463 §8.4），不在本单射程。
- **`input tap`/`swipe` 的 rc**：与 keyevent 同族的无 rc 注入点，属后续批次或
  §2 模型级裁决（helper 副本传播）范畴。
