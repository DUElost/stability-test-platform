# 源扫描棘轮的判据去假阳性：7 处「解析/渲染产物」不是源扫描，债务清单先要是真的（#2639 第三批口径修正）

Status: implemented
Class: testing

- 日期：2026-09-20
- 关联：`#2639`（本族立单，已 CLOSED；本次修的是它的**判据口径**）、
  前两批落地记录 `docs/notes/testing/2026-09-18-source-scan-batch2-agent-cleanup-2639.md`（同族第二批）、
  `#2663`（同思路：清单/数字与现实脱钩比没有数字更危险）、`#2641`/`#2642`（同思路：判据必须落在代码行上）
- 落点：`tests/test_source_scan_anchor_ratchet.py:1`

## Decision

**本来要做的是「site 面迁移」，做完前检才发现该做的是分母口径。** 按第二批记下的优先级
（按「被扫文件是否会搬家」排，不按处数排），第三批应做 `tests/test_site_*.py` 共 11 处。
逐处读代码后：**其中 4 处断言的对象根本不是源文本**——
`tests/test_site_handover.py` 三处是 `render_navigation_page(template_src, ctx)` 的**渲染产物**，
`tests/test_site_install.py` 一处是 `json.dumps(report) + 状态文件` 的**混合体**。
对产物判「某词不存在」是行为断言，它没有真源锚点可编；硬迁移只会**造出假锚点**——
假锚点比空守更糟，因为它看起来像一道防线。

于是本批不做迁移，改做判据：**存量清单必须先全是真债务，后面的批次数量才有意义。**

**口径改成三态溯源**（`_SRC` / `_PRODUCT` / `_UNKNOWN`）：

- 只有**显式登记**为「解析/渲染产物」的消费者才放过，每条带理由（当前 5 条：
  `json.loads`、`json.dumps`、`yaml.safe_load`、`ast.parse`、`render_navigation_page`）；
- 源文本进了**未登记**的函数、或被未知包装（`set(json.loads(src))` 之外的形态）——
  一律 `_UNKNOWN`，而 `_UNKNOWN` **按源文本处理**，照旧判红。

**这个默认方向是本批唯一的安全边界**，来自一条实测反例：我先写的版本是「值脊柱」规则
（只有 `src.read_text().replace(...)` 这类脊柱才算源文本），拿它跑全仓发现
`tests/test_ansible_config_channel_2218.py:50` 被放过了——那里是
`"\n".join(read_text().splitlines() 去注释)` 之后判禁词，**货真价实的源扫描**。
判据少抓下一轮能补，**静默放过正是 #2639 立单要消灭的那件事**。所以改成
「默认判红 + 显式登记产物」，并把它写成夹具 `washed_text.py`（见下）永久钉住。

**两条自证钉子，都不依赖我自己诚实**：

- `test_tightening_only_subtracts_never_adds`：把旧口径在同一份 AST 上重算一遍，逐位点要求
  新绑定 ⊆ 旧绑定。它盯的是**方向**而不是总数——总数下降可能是收假阳性，也可能是开始漏判。
- `test_product_consumers_are_load_bearing`：登记的产物消费者必须在扫描面里真的出现，且必须有理由。
  本轮它当场抓出我自己塞进去的两条投机登记（`yaml.load`、`tomllib.loads`——全仓零使用）。
  死条目就是未来的黑洞：以后任何同名函数读源码判禁词会被直接判成产物而不再受检。

**顺带删掉一条不改变任何判定的白名单**：我原本加了 `_TEXT_PRESERVING_ATTRS`（join/splitlines/
replace…）来「保留洗过的文本」。M2 变异证明：把 `join` 从白名单里拿掉，判定**完全不变**
——因为保守默认已经把这类形态判成源文本了。既然它不承载任何区分，就是第二套口径的雏形，
当场删除；放过路径从此只有「显式登记」一条。

**结果**：存量 **25 文件 / 59 处 → 23 文件 / 52 处**（当场派生，与 `BASELINE` 集合完全相等）。
被整体摘掉的是 `backend/tests/test_schema_sync_guard.py`（`set(json.loads(...))` 判键存在性）
与 `tests/test_site_handover.py`（渲染产物）。**这不是文件级豁免**：M6 变异往
`test_site_handover.py` 里塞一条真源扫描，棘轮立刻 growth 红并点名该文件。

## Alternatives

- **把这 7 处写进 `EXEMPT` 或做文件级豁免**：否。`EXEMPT` 被既有钉子钉死只能含两个元文件；
  文件级豁免的方向在第二批已经证明是反的（导入越彻底越能藏）。
- **不动判据，留着假阳性**：否。清单失真的直接后果是第三批会被逼着给渲染产物编假锚点，
  而且「还剩多少活」这个数字会一直骗下一轮排期。
- **上完整数据流/类型推断（判出变量是不是 `str`）**：否。判据越聪明，静默漏判的面越大，
  而这里需要的是**每一次放过都能被指出来**。显式登记 + 保守默认是可审计的最小方案。
- **反过来：默认放过未知形态，白名单保留源扫描**：否。那等于把 #2639 的失效机制写进判据本身。

## Verification

- 全量实跑（`.wt/stp-2639-b4`，base `86048ccb`）：根 `tests/` → **1613 passed, 11 warnings**（146s）；
  `tests/test_source_scan_anchor_ratchet.py` → **7 passed**；`tests/test_source_anchor_helper.py` → **9 passed**。
- 口径派生核对：`scan_offenders()` = **23 文件 / 52 处**，与下调后的 `BASELINE` 集合完全相等；
  7 处释放逐条读码确认（2 处解析结构 + 3 处渲染产物 + 1 处 yaml 解析 + 1 处序列化混合），
  `tests/test_ansible_config_channel_2218.py` 的 `join` 去注释形态**确认仍判红**。
- **8 条变异，逐条判红且红在正确的因上**，全部还原后基线复跑绿：
  M1 未知消费者当产物 → 夹具红（`mystery.py`）；M2 未知包装当产物 → 夹具红（`washed_text.py`）；
  M3 撤 `render_navigation_page` 登记 → growth 红点名 `tests/test_site_handover.py`；
  M3b 撤 `json.loads` 登记 → growth 红点名 `backend/tests/test_schema_sync_guard.py`；
  M4 登记死条目 → `test_product_consumers_are_load_bearing` 红；
  M5 忘记下调基线 → stale 红；M6 被放过的文件写真源扫描 → growth 红（证明不是文件级豁免）；
  M7 让判据不再做减法 → `test_tightening_only_subtracts_never_adds` 红。
- 白名单删除的前后对照：删前/删后均为 23 文件 / 52 处（**行为中性**，故删）。
- 门禁：`run_gates.py check:quick` / `check:pr` 结果见 PR；未跑完即标 pending。

## Revisit

- **迁移批次仍在**：真实存量 23 文件 / 52 处。按「被扫文件是否会搬家」排，下一批候选是
  `tests/test_update_agent_playbook.py`(4)、`tests/test_site_install.py`(3)、
  `tests/test_agent_priv_boundary.py`(3)——它们扫的是活动源，属本棘轮的射程正面。
- **若某文件同时含渲染产物断言与真源扫描**：正确做法是把两条断言拆开（各归其位），
  而不是往 `_PRODUCT_CONSUMERS` 加条目——加条目会让整个文件的同类形态一起失去检查。
- **`_callee_key` 的已知边界**：方法链挂在任意表达式上时只能按末段属性名判
  （`a.read_text().replace(...)` → `replace`）。因此登记名带点号时按**完整名**比对
  （`json.loads` 不为 `loads` 背书，M4 即为此而设）。若将来出现同名产物函数误放过，
  按 M4 的口径重审登记，不要改默认方向。
- 本批判据只处理「否定断言」形态（`not in`）。**正向**的 `assert 词 in 源码`（同样的静默失效机制）
  不在射程内，至今无判据——那是本家族剩下最大的一块，需要单独立面再动，不要顺手加进这个棘轮。
