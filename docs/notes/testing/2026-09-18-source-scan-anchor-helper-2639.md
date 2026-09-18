# 源扫描型守卫抽公共锚点助手 + 棘轮：让「用例已过期」与「防线真回归」可区分（#2639）

Status: implemented
Class: testing

- 日期：2026-09-18
- 关联：`#2639`（本单）、`#2598`/`#2608`（前两例）、`#2630`/`a00864e8`（第三例的产品侧修复）、
  `#1520`（把逻辑搬走的那批下沉，三例的共同成因）、`#2025`（否定断言字面量抓不到赋值形态）、
  `#2641`/`#2642`（判据必须落在代码行上——本单沿用其 AST 口径）、`#1569`/`#1707`（根 `tests/`
  前移进 required check）

## Decision

### 本质问题

不是「这三个用例坏了」，而是**一族守卫的有效性没有任何判据**：`assert "<字面量>" not in src`
在被扫逻辑搬走后**恒真**，守卫还绿着，但覆盖的是零（#2639 第 3 例红的是正向断言，
两条否定断言静默失效一个完整窗口）。所以修法是给这一族补一个**结构性判据**，
而不是再修一个用例——再修第三个也只是等第四例。

三种失败形态按「会不会响」分类，本单只消灭第三类：

| 形态 | 旧行为 | 新行为（用助手后） |
|---|---|---|
| 被扫逻辑搬走、`import` 失败 | collection error | 不变（已响亮） |
| 正向字面量搬走 | 恒红，但读起来像产品回归 | `AnchorDrift`：消息第一行写「用例已过期」 |
| **否定断言恒真** | **静默通过** | 锚点先失败 → 同一消息；红侧不再骗人 |

### 一、助手 `tools/dev/source_anchor.py`

- `SourceGuard.of_module(mod)` / `of_repo_path(rel)` 取代 `Path(mod.__file__).read_text()`：
  **文件不存在也按锚点漂移报**（退成 `FileNotFoundError` 或恒真都是同一种失真）；
  `of_module` 用 `inspect.getfile` 并对仓库外模块判 `GuardMisuse`（站外代码不受本仓重构约束）。
- 两步式：`.anchored(needle[, expect=n])` 先证「逻辑还在这个文件里」，之后才能
  `assert_present` / `assert_absent(…, why=…)` / `assert_count`。未声明锚点就断言 → `GuardMisuse`
  （把恒真断言的入口封死）；`assert_absent` 的 `why` 必填（写不清防的是谁就不该存在）。
- 三类异常都派生自 `AssertionError` ⇒ pytest 里一律 failed（不退化成 collection error），
  但前缀常量互不相同 ⇒ 看红第一行即可判因。`anchored()` 是**前置判据不是被测行为**，
  计数也进判据（只判 ≥1 会放过「被复制/部分搬走」）。
- 落点在 `tools/dev/`：`tests/`（仓库根）与 `backend/tests/` 两棵树都以仓库根为
  `sys.path[0]`，同一模块两边可导入——这正是必须的，因为 offender 分布在两棵树里。

### 二、棘轮 `tests/test_source_scan_anchor_ratchet.py`（放根 `tests/`，不放 `backend/tests/`）

`backend/tests/` 只在**夜间全量** `backend-test` job 跑（`ci.yml` 里 `if: github.event_name != 'pull_request'`），
把棘轮放那儿就等于复制 #2639 抱怨的「红只有夜间可见」。根 `tests/` 在 required check
`pr-agent-tests` 里（#1569/#1707 前移的那条路径），棘轮因此与它保护的家族**不在同一可见性层级**——
这是刻意的：判据要比被约束的东西更早响。

- 判据按 **AST**：函数体内把 `read_text()` / `getsource()` 结果绑到变量（或内联调用），
  且对该变量做过 `assert … not in <它>` ⇒ offender。注释里的同形文本不满足判据
  （#2641/#2642 的口径）。
- 双向收紧：`found - BASELINE` 非空即红（不再新增存量），`BASELINE - found` 非空也红
  （基线只能缩短，改完必须回来下调）。后者顺带堵死「在文件里塞一行 `# source_anchor`
  冒充已迁移」——豁免按**是否真的 `from tools.dev.source_anchor import`** 判定。
- 反空转：`_default_roots()` 断言三棵扫描目录都在场；`test_scan_is_load_bearing`
  断言命中数 ≥50 且一个已知实例必须在名单里。**一个 offender 都扫不到即红。**
  这条不是形式主义：#2639 自己数「12 个文件」用的 `git grep … -- 'tests/**/*.py'`，
  git pathspec 不认未加 `:(glob)` 的 `**` ⇒ `tests/` 整棵树**静默零命中**
  （实测 `:(glob)` 版命中 81 个文件）。真实家族是 28 个文件 / 78 处否定断言（迁移两例后基线 26 / 75），
  不是 12——一个不响的判据比没有判据更危险。

### 三、示范迁移两处（含第 3 例本体），其余进基线

`backend/tests/services/test_device_log_event_uniview_upload_1956.py`（#2639 第 3 例的当事人）与
`backend/tests/api/test_watcher_summary_uniview_1956.py`（同为「正锚点 + 否定断言」形态，
且 `#1520` 切片时注释里就写过「守卫跟着所有权走」——但没人检查它跟上了没有）。
其余 26 个文件留在基线里逐步收口：它们分布在 `backend/agent/tests/`（热更新/刷机脚本守卫）与
站点安装链守卫，其中多处在 cursor / codex / claude 的在窗 `effective_scope` 内
（`tests/`、`backend/tests/services`、`backend/tests/api`），一轮全改必撞队列。

### 四、约定沉淀进 `docs/development/testing.md` §7

与既有的「等异步状态」小节并列（同一类「写法导致静默失效」的问题），含写法示例、
为什么放根 `tests/`、以及 pathspec 静默零命中的教训。

## Alternatives

- **只写文档约定，不加棘轮**：被否。#2639 的三例全部发生在**已有**「守卫跟着所有权走」这类
  注释的仓库里——约定不产生判据就只是提醒，而提醒已经被证明拦不住。
- **一轮迁移全部 28 个文件**：被否。跨三棵树、78 处断言，且 26 个文件与在窗 Execution
  的声明面重叠；强行改完会让 FIFO 队列里出现 CONFLICTING 队首（#2624 正是那个症状）。
  棘轮的语义本来就是「存量只减不增 + 新写的必须走正道」。
- **判据用 `git grep`/正则**：被否，两条实测理由：① 子串匹配会被注释满足（#2641 同族）；
  ② #2639 的规模统计本身就是 grep 类判据静默零命中的产物。
- **把助手做成 pytest plugin / fixture**：被否。家族横跨 `tests/`、`backend/tests/`、
  `backend/agent/tests/` 三套 rootdir 与两套 CI job，插件接线面比一个纯 stdlib 模块大，
  而它需要的只是「读源码 + 抛 AssertionError」。
- **让 `AnchorDrift` 不退化成 `AssertionError` 子类（用 `pytest.fail`）**：被否。
  collection error / setup error 在 CI 汇总里常被当成噪音（第 1 例的教训），
  failed 才是能被看见的状态。
- **顺手把 `docs/development/testing.md` 的「等异步状态」小节合并进来**：被否，非本单范围。

## Verification

只列实跑命令与结果。

- `env -u DATABASE_URL …python -m pytest tests/test_source_anchor_helper.py tests/test_source_scan_anchor_ratchet.py <两个迁移实例> -q`
  → **18 passed**（助手 9 + 棘轮 4 + 实例 5）。
- 判别力变异 **9 条全部变红**，还原后棘轮基线绿、套件 18 passed：
  K1 新增未迁移的否定断言用例 → 「新的源扫描…」；K2 从 `BASELINE` 删一项（谎称已迁移）→ 红；
  K3 `SCAN_DIRS` 去掉根 `tests/` → 「这些文件已不含该形态」；K4 扫描目录改名 →
  「扫描目录消失」；K5 判据不再认 `read_text` → 判别力用例红；
  H1 助手去掉 `_require_anchor` → 红；H2 把两类红的前缀改成同一个 → 红；
  E1 把实例改读一个不含锚点的真实模块 → `AnchorDrift: 用例已过期`；
  E2 在产品代码里回潮一行 `row.state = ev.state` → `FormRegression: 防线回归`。
- **本单自己写坏过两次判据，都被变异当场抓住（留作证据）**：
  ① 首版豁免用「文件文本含 `source_anchor`」子串判定 → 注释即可绕过，已改 AST 导入判定；
  ② 首版「三类红可区分」只比消息前 20 字符 → 前缀常量相同仍判绿（H2 首跑 GREEN），
  已补「三个前缀常量本身互不相同」断言。
- `python tools/dev/check_governance_surface.py --check` → 见下（纯工具/测试改动也单独复跑）。
- `python scripts/run_gates.py check:quick` / `check:pr`：在提交后的干净树上跑，结果写进 PR 描述。
- pending：26 个基线文件的实际迁移（按棘轮逐批改，优先级见 `test_agent_installer.py` 这类
  同时含 3 处否定断言的文件）；`backend/agent/tests/` 4 个文件是否允许导入 `tools.dev.*`
  需先确认 agent 侧导入边界判据（未测，不在本单动）。

## Revisit

- **needle 仍是文本匹配**：`assert_absent` 对产品代码里的**注释/文档字符串**同样会判红
  （假阳性方向，不是漏报）。要收严需把 needle 升成 AST 形态判据（#2642 那条路），
  成本明显更高，且会把「字面量必须覆盖赋值形态」的 #2025 教训重讲一遍。
- **判据未覆盖的绑定形态**：`self.assertNotIn(x, src)`、海象 `if (src := p.read_text())`、
  跨函数传参（把源码文本当参数传给另一个函数再断言）都不在 AST 判据里。
  下一版最自然的扩展是把 `_source_bound_names` 扩到 `with` / 形参，但需要先有命中实例。
- **基线是 2026-09-18 的快照**：若 owner 认为「夜间才跑的文件不必进 PR 门禁」，
  `SCAN_DIRS` 可缩到 `("tests",)`——那会同时丢掉对 `backend/tests/` 4 个文件的约束，
  故不建议；替代形态是给 `backend/agent/tests/` 单列一条基线（不同 CI 可见性、不同判据强度）。
- **助手是否该被产品代码用**：目前只服务测试。`tools/dev/` 里同一位置的门禁脚本
  （`check_*.py`）也用 AST，若将来产品侧需要「读自身源码」的能力，应另立判据而非复用本模块。
