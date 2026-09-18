# main 上两条恒红/恒缺的用例：符号下沉与响应信封的形状漂移（#2598）

Status: implemented
Class: testing

## Decision

`origin/main`（`7436b221`）上有两条与业务无关、纯粹由**形状漂移**造成的坏用例。
两者都在干净 worktree 里实测过，不是推断：

| 用例 | 形态 | 成因 |
|---|---|---|
| `backend/tests/api/test_artifact_download.py` | **collection error，整模块 0 条** | `_artifact_download_target` 随 #2420「产物下载双路由收敛为一份实现」下沉到 `backend/services/job_artifact_download.py`，用例仍 `from backend.api.routes.runs import …` |
| `test_results.py::TestRiskVocabularyParity::test_same_level_across_summary_trend_and_report` | 恒红（`assert None == 'S'`） | `e6943f4e`（PR #2543，#2420 第 3 项）把 `GET /runs/{id}/report` 补成 `ApiResponse[RunReportOut]` 信封，而 #2494 写的词表对拍仍按**裸 DTO** 读顶层 |

第二例要先判「谁错了」：**产品是有意的**——`frontend/src/utils/api/runs.ts:13` 写明
live 与 cached 两条同为 `{data, error}` 信封、`unwrapApiResponse` 一处解包，
`backend/tests/api/test_runs.py` 也已经在 #2543 当轮改成钉 `{"data","error"}`。
坏的是**没跟着改的跨形状对拍用例**，所以修用例、不动接口。

改法：

1. **导入跟到 service 层**：`from backend.services.job_artifact_download import
   _artifact_download_target`，保留原有 `try: backend.* / except ModuleNotFoundError:
   <裸包名>` 双写法——文件顶部把 `backend/` 插进了 `sys.path`，老布局的导入形态还在用，
   删掉兼容壳会在别的调用形态下红（这不是"顺手清理"）。
2. **对拍按各自形状解到位**：钉 `set(report_body) == {"data", "error"}` +
   `error is None`，再取 `report_body["data"]`，然后**两侧各自钉死再比相等**
   （报告侧单独钉 `== "S"`，最后才与列表侧比相等）。顺序很重要：只写 `==` 时，
   两侧同时读空也是「通过」。
3. **过期注释改掉**：用例里那段「同一文件三种形状…本单不改信封」的自述已经被 #2543
   变成假话——留着它，下一个人会以为信封是待办而不是现状。
4. **不加新门禁**：防复发的正解是 #2551（把全量套件前移），本单只把主干上已成事实的
   红拿掉，让夜间 `backend-test` 重新有判别力。

## Alternatives

- **把 `/runs/{id}/report` 退回裸对象**（让测试「原样绿」）——放弃：那会推翻 #2420 第 3 项
  的裁决，并把前端与所有脚本重新推到「同一资源两套形状」的老坑。
- **给用例加 `xfail`/`skip` 让它先不红**——放弃：#2568 的教训就是这个形状「红着但没人看」，
  标成 xfail 更糟（红变绿，判别力归零，且没人再有动力修）。
- **把信封解包做成共享 helper 再用在测试里**——放弃：本仓的测试侧解包口径已经是
  「就地断言 `{"data","error"}` 再取 `data`」（`test_runs.py` 同形），为一个断言引入
  新抽象只会多一处要和后端同步的地方。
- **只做第 1 项、把词表对拍那条留给 #2494 的作者**——放弃：两例是同一族（形状漂移把
  主干染红），一次修完才知道「同类还有几处」这个清点到底该怎么做。

## Verification

- 三条用例判别力（**收集数**与红/绿双向）：
  - `test_artifact_download.py`：修前 **0 collected + 1 collection error** → 修后
    **4 collected / 4 passed**；反向 `N3` 把导入改回 `backend.api.routes.runs` →
    立刻回到 collection error（rc=2）。收集数就是这条红的钉子，绿而空等于没修。
  - 词表对拍：修前 main 上 **1 failed**；修后 `test_artifact_download.py` +
    `test_results.py` + `test_runs.py` 合跑 **23 passed**（19 → 23 的差额即那 4 条
    重新出现的用例）。
  - `N1` 把解包退回原写法（`report.get("risk_summary")` 读顶层）→ 该用例红。
  - `N2`（判据 2 的反向自证）把解包改成容错读法 `report_body.get("payload") or {}`
    ——即「错了也不炸」的那种写法 → 该用例**仍然红**，证明「绿但没测到东西」
    在本用例里不可能出现。
- 现场根因核对（不是读注释猜的）：`git grep -n "_artifact_download_target" origin/main`
  只有 service 里一处定义 + 用例里两处旧导入；`git log -S "ApiResponse[RunReportOut]"
  -- backend/api/routes/runs.py` 唯一命中 `e6943f4e`。
- 基线自证：同一条对拍用例在 `6a04074a`、`b1bf88b0`、`7436b221` 三个 main 上都是红的，
  排除「本单改动引起」。
- pending：`check:quick` / `check:pr` 结果见 PR 评论。

## Revisit

- **本单的判别力只在「跑到了这条用例」时成立**：`backend/tests/**` 不在 PR 路径
  （`ci.yml` 的轻量 job 只跑 agent 侧），所以同类漂移仍要等夜间全量才被发现——
  这是 #2551 的账，不在本单顺手补。
- **#2568 第 2 项（同类目标清点）仍未做**：本单确认的口径要跨三种形态——
  `patch("…")` 字符串、`from … import <name>` 的符号存在性、**响应信封层级**。
  真要固化成静态核对（AST 扫 `tests/**` 的字符串目标与 import 目标是否仍存在于目标
  模块顶层），可复用 `tools/dev/env_inventory.py` 的扫描器形状；但那需要一个**稳定的
  全量绿基线**才有意义（基线一红，门禁只会被人关掉），顺序上应在 #2551 之后。
- `test_artifact_download.py` 的 `try/except ModuleNotFoundError` 双写法是历史兼容壳。
  如果哪天确认没有任何调用形态再用裸包名（`sys.path.insert` 那行一并删），
  这个壳该整体退役，而不是继续在每个符号搬家时同步两处。
