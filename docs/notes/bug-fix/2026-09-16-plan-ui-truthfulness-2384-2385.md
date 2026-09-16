# UI 把「还没发生」渲染成「已经好了」：新建 Plan 状态芯片 + 选机空态版本槽（#2384 / #2385）

Status: implemented
Class: bug-fix

## Decision

**1. 新建 Plan 的顶栏芯片（#2384）**：`usePlanEditHeaderSlot` 的芯片原来只有
`form.isDirty ? 未保存 : 已保存` 二分支——`isDirty` 是「相对基线有没有改动」，
新 Plan 的基线是空模板，于是 `/orchestration/plans/new` 一进来就宣称「已保存」，
而**服务端此时没有这条 Plan**（同屏「创建」还是灰的、左侧卡片写着草稿）。

改为三态：

| 状态 | 芯片 |
|---|---|
| `isNew && !isDirty` | 灰底「待创建」（`STATUS_CHIP.muted`） |
| `isDirty` | 黄底「未保存」（不变） |
| `!isNew && !isDirty` | 绿底「已保存」（不变） |

「待创建」沿用设计系统已有的 muted 芯片 token，不新增视觉语言；左侧草稿卡片、
灰着的「创建」按钮与它三者说法一致。

**2. 选机空态的版本槽与出口（#2385）**：

- `PlanExecutePage` 里 `versionConsistent = selectedDevices.length === 0 || …`，
  0 台时恒真，`ExecuteCommandBar` 据此渲染**绿色「0 版本 · 一致 ✓」**——空集不是
  「已验证一致」。改为 `selectedCount === 0` 时渲染中性「版本 —」，与同条
  「预检 —」的缺省态一致；选中样机后仍照常给出「N 版本 · 一致 ✓ / 冲突」。
  判据本身不动（它表达的是「已选集合内是否一致」，在空集上真空成立是对的），
  **改的是渲染**：真空值不该画成通过态。
- 空态「请先添加测试设备」指向的动作页是 `/devices`，但只是纯文本。用
  `EmptyState` 已有的 `action` 槽加一个 `<Link to="/devices">添加测试设备</Link>`，
  文案改为「设备列表为空。添加并接入测试设备后，这里才能选机。」——出口与解释同处。

两处同族：都是**把「无数据/未发生」当成「已通过/已保存」**渲染（与 #2361 的
「404 被说成网络故障」同属一类：界面陈述与真实状态不符）。

**批次边界**：同批候选 #2027（`line-clamp` 失效 + 错误分支对比度）**未纳入**——
它的错误分支修复与本会话刚开的 PR #2403 改同一段代码（同一文件同一区域），
并行改会在合入时撞车，等 #2403 合入后再做。

## Alternatives

- **A. 新建 Plan 芯片显示「未保存」**：否决。什么都没改就喊「未保存」是另一种
  错误陈述（用户会去找根本不存在的改动）；而且「草稿还没落库」与「改坏了没存」
  的运维动作不同——前者是「点创建」，后者是「点保存」。
- **B. 把「待创建」做成可点击（直接创建）**：不做。顶栏已有主 CTA「创建」，
  再放一个入口会让「创建」的语义分叉；本单只修事实陈述。
- **C. 在数据层把 `versionConsistent` 在空集时改为 `false`/`undefined`**：
  否决。判据本身没错（空集内元素同版本，真空成立），错的是把它画成绿色通过；
  改了判据会让依赖它的其它消费方拿到假值。渲染层收口最小且不影响语义。
- **D. 只改文案不加出口（#2385 第 2 点）**：否决。issue 实测的痛点就是「文案指向
  `/devices` 但用户得自己翻译成路由」，纯文案改动不解决它。
- **E. 把 #2027 一起做**：见上（批次边界），改同一段代码的两个在飞 PR 会在合入
  时冲突，宁少不多。

## Verification

- **红绿差分（先证伪再实现）**：三条新守卫在 `git checkout` 回基线实现后全部失败——
  `renders a neutral 版本 — slot …`、`shows 待创建 (not 已保存) …`、
  `gives the empty device pool a link to /devices …`（`3 failed | 69 passed`，
  其余 69 例在基线上本就是绿的）；换回新实现后全绿。
- **测试**（`frontend/` 下运行）：
  - `npx vitest run src/pages/orchestration src/components/execution src/pages/execution`
    → **180 例：178 passed / 2 failed**。两例失败为**存量环境差**（本地 Node v24 vs
    CI Node 22 的 `PlanRunDetailPage.test.tsx` socket/抽屉两例），与本单无关，已在
    未改动基线上复现。
  - 单跑改动面：`PlanEditPage.test.tsx` + `ExecuteCommandBar.test.tsx` + 
    `PlanExecutePage.test.tsx` → **72 例全绿**。
- **类型与 lint**：`npx tsc --noEmit` 通过；`npx eslint <本单 6 个文件> --max-warnings 0` 通过。
- **未做**：未做浏览器实测；「待创建」芯片的颜色/边框只按设计系统 token 取值，
  未做视觉走查（本机无前端跑起来的隔离环境）。

## Revisit

- **#2027 顺延**：等 PR #2403 合入后单独做（它同时要修错误分支的对比度
  `/60`、`/70`）——那一段正是 #2403 改过的行。
- **同类扫描**：本单只按 issue 覆盖了两处。其它「空集/未发生 → 绿勾」形态
  （如各卡片的「全部正常」在零样本下）要不要一起收，等有现场证据再逐个做；
  判据是「这个绿勾背后有没有真实样本」。
- **`versionConsistent` 的其它消费方**：本单只改渲染。若将来有消费方（例如
  预检阻塞逻辑）需要区分「空集真空」与「真的同版本」，应在数据层显式建模，
  而不是继续依赖布尔真空——届时改判据要连同本单的渲染分支一起看。
