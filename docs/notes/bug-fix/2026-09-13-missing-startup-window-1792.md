# #1792（第三类）missing 的启动窗口误报

Status: implemented
Class: bug-fix

## Decision

`scripts/ci/pr-automerge-queue.sh` 的 `MISSING` 分支引入**启动窗口**判据：只有当该 PR
的 check **已运行超过宽限期**（`MISSING_GRACE_SECONDS=600`）而某 required check 仍无
条目时，才判为真 missing 并告警；窗口内视为「尚未注册」，**不告警、也不 resolve 存量
告警**。补 4 例回归测试（15 → 19）。

判据数据源用 **rollup 自身已有的 `startedAt`**——无需跨轮持久化（reconcile 是无状态
job），也无需 check→workflow 映射的额外 API 调用。

## 为什么 #1796 之后还需要这一单

#1796 修的是 `COMPLETED/NEUTRAL` 被当失败；本单修的是 `MISSING` 被无条件当异常。
**#1796 合入后告警未止**（10:34–11:30 持续，约 4 分钟一条），且我此前给的「残余告警
来自在飞 PR 的旧脚本」解释**被证伪**：

| 告警 | 时刻 | 队首 | 产生该告警的 run sha | 含 #1796 的 `a46db3de`？ |
|---|---|---|---|---|
| #1857 | 11:14:49 | #1855 | `c63740eb`（11:14:23） | **含** ❌ |

即修复对**这一类**形态无效。此更正已发在 issue 评论中。

## 根因：`CodeQL` 是独立 workflow，注册晚于 CI

`CodeQL`（github-codeql 默认 setup）的 check 条目**晚于** CI job 注册进
`statusCheckRollup`。实测三个 PR 的 `startedAt`：

| PR | lint 启动 | CodeQL 启动 | 差值 |
|---|---|---|---|
| #1855 | 11:14:42 | 11:15:15 | **+33s** |
| #1858 | 11:21:41 | 11:22:01 | +20s |
| #1860 | 11:31:10 | 11:31:33 | +23s |

而告警 #1857 发生于 **11:14:49**——比 CodeQL 注册（11:15:15）**早 26 秒**。该窗口内
条目**确实不存在**，但那是**每次 PR 都会出现的正常启动时序**。

**为什么此前修不掉**：#1764 把「进行中」移出告警集时，**保守保留了 `MISSING → 告警`**，
理由是「该 check 根本没被注册/上报，需人工排查」。该理由默认「无条目 = 异常」，
而 CodeQL 证明**无条目也可以是正常时序**。

## Alternatives

- **方向 3：查 workflow 触发状态（`gh run list --workflow=CodeQL --branch …`）** →
  判据最准（「已触发未注册」= 正常窗口，「未触发」= 真异常），实测可行。**本单未采纳**：
  需维护 check→workflow 映射表（lint/CodeQL/pr-* 分属 CI / CodeQL / PR Agent 三个
  workflow），且 reconcile 每 ~15s 跑一次（per PR event），额外 API 调用会放大配额消耗。
  记入 Revisit——若本单宽限判据出现误判，方向 3 是升级路径。
- **方向 1 的朴素形态：跨轮记录首次观测到 MISSING 的时刻** → 否决：reconcile 是无状态
  一次性 job，跨轮状态须落盘（复用 issue body fingerprint 或新增存储），成本高于收益。
  **本单找到免持久化的等价判据**（rollup 自带 `startedAt`），故不需此路。
- **按固定数值阈值（如「PR 创建后 N 分钟」）** → 否决：PR 创建到 CI 启动的延迟受队列
  与审批影响（`approve-pr` 等），固定阈值与真实启动时刻脱钩；用 `startedAt` 直接锚定
  「check 何时真正开始跑」，是更贴合的基准。
- **去掉 `MISSING` 告警（一律不告警）** → 否决：那会漏掉「workflow 被禁用/改名/审批
  卡住」这类真异常——正是 #1764 当初保留它的理由。本单是**加时间维度区分**，不是删除。
- **同步修 `queue_head_telemetry.py`** → 本单不做：它是**按需诊断工具**（非每 PR 自动
  运行），启动窗口内的瞬时误判不产生 issue，影响面远小于告警刷屏。记入 Revisit。

## Verification

- `python -m pytest tests/test_automerge_queue_alerts.py -q` → **19 passed**（原 15 + 新 4）；
- **红绿双向**：新测试在 #1796 版脚本上 → **2 failed**
  （`test_check_not_yet_registered_within_startup_window_does_not_alert` /
  `test_no_checks_registered_at_all_is_startup_window`）；修复后 → **19 passed**；
- **宽限判据实测**：`startedAt=2026-09-13T11:14:39Z`（告警时刻距今 ~10s）
  → 判为**启动窗口，不告警**；同一时间戳在 ~32 分钟后 → 超出宽限（正确，因为届时该
  check 确已注册，若仍缺失就是真异常）；无 `startedAt` → 判为启动窗口；
- **真实告警未被削弱**：`pr-agent-tests: COMPLETED/FAILURE` 仍走 failed 分支告警
  （`test_completed_failure_still_opens_alert` 等既有用例保持通过）；
- **既有 15 例全部保持通过**——含 #1246 真实停摆告警、NEUTRAL 放行、pending 不告警；
- `python -m pytest tests/ -q --ignore=（两个容器文件）` → **303 passed**；
- `queue_head_telemetry --self-test` → **8 组红绿样例双向通过**（未受影响）；
- `bash -n scripts/ci/pr-automerge-queue.sh` → 语法通过；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿。

## Revisit

- **宽限值 600s 的依据**：实测注册延迟 20-38s，取 10 分钟留 ~15× 余量；同时远小于
  「PR 在窗数小时」的尺度，故不会长期静默真异常。**若出现「真 missing 被宽限掩盖
  超过 10 分钟」的案例，应收紧**；反之若注册延迟因 workflow 增加而变长，应放宽。
  该常量已具名（`MISSING_GRACE_SECONDS`）便于调整。
- **`queue_head_telemetry.py` 未同步**：它仍把启动窗口内的 `MISSING` 计入 blocking
  （`reason_code=REQUIRED_CHECK_FAILED`）。作为按需诊断工具影响有限，但若将来被接入
  告警或自动化，**必须同步本单判据**，否则会以另一种形态复现刷屏。
- **方向 3 作为升级路径**：若宽限判据被证明不足以区分（例如 workflow 触发后长时间
  queued 超过 10 分钟），改用「查 workflow 触发状态」——判据更准，代价是映射表与
  API 配额。届时可只对**仍未注册**的那一个 check 做额外查询，而非全量。
- **三份口径的实现漂移**：本判据现存于 bash（已修）与 python（未修）。这是第 3 次
  「两个工具共享盲点」（#1761 / #1792 NEUTRAL / 本单）。**第三次已到**——建议下一步
  让告警脚本直接消费 telemetry 的判定结果，或将判据抽为单一可测函数供两侧调用，
  而非继续各自实现。
