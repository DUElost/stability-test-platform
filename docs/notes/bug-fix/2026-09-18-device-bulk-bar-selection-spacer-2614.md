# #2614 设备页全选后悬浮批量条压住分页行：占位补偿 + 避让规格收为公共约定

Status: implemented
Class: bug-fix

日期：2026-09-18 ｜ Harness：codex ｜ 分支：`fix/2614-device-bulk-bar-selection-spacer`

## Decision

问题面：`fixed bottom-4` + `z-40` 的批量条是**覆盖层**，外层 `pointer-events-none` 只把两侧留白
让出命中区，真正接事件的是居中的内层实条（`pointer-events-auto`）。滚到底时表格最后一块可点区域
（分页行）必然落进它的覆盖带。主机页在 `be51f2c6` 单独补过一次占位，但**规格没有落成约定**
（无 issue、无测试锚定来源），设备页因此以同一形状复发：1280/1366 宽下「下一页」的坐标点击被吞，
最坏情况命中「取消选择」，刚建立的整页选中被静默清空。

本单做两件事：

1. **补设备页的避让**（`frontend/src/components/device/ExpandableDeviceTable.tsx:729`）：
   `selectable && selectedIds?.size > 0` 时在**表格卡片之外**渲染 `BulkBarSpacer`，
   与主机页同规格，不改分页/选中逻辑。
2. **把避让规格收成一处**（新增 `frontend/src/components/ui/bulk-action-bar.tsx`）：
   `BULK_BAR_OUTER_CLASS` / `BULK_BAR_INNER_CLASS` / `BULK_BAR_SPACER_CLASS` + `BulkBarSpacer`。
   两张表的批量条改用常量（类名字符串逐字未变，纯去重），两张表的占位改用同一组件。

判据边界（写清楚，避免把「测不到」说成「测到了」）：jsdom 无布局引擎，Playwright 常规
`locator.click()` 又会 `scrollIntoViewIfNeeded` 重算落点——**遮挡本身只能靠坐标级点击复现**
（issue 正文的 `/tmp/b17/geo*.js` 取证）。因此本单的用例钉的是**补偿的存在性、门控条件与相对位置**
（占位在分页行之后、在卡片之外）+ **规格不得各自漂移**，不声称钉住了命中测试。

## Alternatives

- **只补设备页的占位（issue 的最小方案第 1 条）**：能关掉这条 bug，但第 3 张表格页仍会复发——
  同一条几何此刻在两个文件里各抄一份，`#360` 已经证明它们会漂（`max-w-5xl` vs `max-w-4xl`）。
  故选「补偿 + 公共约定」，代价是多改 3 个文件（均为等价替换，无视觉变化）。
- **给内层条加 `pointer-events-none`**：会把「取消选择」本身变成不可点，issue 明确排除。
- **`scroll-padding-bottom` / 分页行 `mb-20`**：只对"滚动落点"有效，遮挡区仍然存在（点击仍会被吞），
  且与主机页已有形状不一致。
- **由条体测高、动态撑开（ResizeObserver 注入 CSS 变量）**：能消掉 `h-40` 这个魔数，但要引入
  观测生命周期与跨层上下文，超出一个 P2 点击被吞的必要性——登记为 Revisit，不做。
- **加一条像素级 E2E**：本仓 `frontend` 无 Playwright/Cypress 基建（`git ls-tree origin/main` 无
  playwright/cypress 配置），为此单引入一套 E2E 基建属于另立单，不塞进本单。

## Verification

- **等价性逐字核对**：新常量与 `origin/main` 上两页原有的 `className` 字面量**逐字节相同**
  （脚本比对 OUTER/INNER/占位三处，并确认主机页与设备页本来就一模一样）——
  这条决定它是不是纯去重，`toBe` 类断言自己证明不了自己。
- 单测（vitest，本机 `npx vitest run`）：
  - `frontend/src/components/device/ExpandableDeviceTable.test.tsx` 6 → **8 passed**（新增 2 条：
    选中→占位存在且在分页行之后、卡片之外；不可选→不渲染）；
  - 新增 `frontend/src/components/ui/bulk-action-bar.test.tsx` **2 passed**（两条批量条的
    外层/内层类名与共享常量逐字相等；占位块 testid/`aria-hidden`/共享高度）；
  - `frontend/src/components/network/ExpandableHostTable.test.tsx` **21 passed**（原 `toHaveClass('h-40')`
    升级为断言共享常量，行为不变）；
  - 前端全量：**125 files / 1003 tests passed**。
- 新增静态守卫 `tests/test_frontend_bulk_selection_guard_2614.py`（离线、秒级）**2 passed**：
  ①覆盖层几何字面量只允许出现在共享模块；②渲染了批量条的页面，其可选中表格必须真的
  `<BulkBarSpacer>`（判据取 JSX 渲染，不取「文件里出现过这个标识符」）。
- 变异自证（**全部 on-target，逐条改回**）：
  - 静态守卫 `N1` 保留 import、渲染换成手抄字面量 → 红；`N2` 整块补偿删除（=原 bug 形状）→ 红；
    `N3` 门控条件写反 → 静态守卫**不红**（静态检查看不见逻辑，由 vitest 用例负责），如实登记；
  - 用例 `M1` 删除设备表占位 → `reserves bottom clearance…` 红；`M2` 条件写反 → 同条红；
    `M3` `h-40→h-24` → 共享规格用例红（其余用例只比对常量、不重复钉死数值，层次有意）；
    `M4` 设备批量条手抄几何（模拟漂移）→ 平价用例红。
- 门禁：`check:quick` 11 gates（首轮 `eslint` 红 2 条：未用变量 + 多余的 `eslint-disable`，
  已修后复跑）、`check:pr` 20 gates。
- **本单一处流程错误，记下来因为它会造出假绿**：Note 落笔的时机越过了正在运行的 `gov-surface`
  （它排在 `ruff/eslint/tsc/knip/…` 之后），于是那轮「20 gates 全绿」对应的是一个**从未存在过的仓库状态**；
  CI 的 `lint → 治理面结构检查(C-G1 L0)` 如期报 S10 两条（缺 `Status:` 头、`Class:` 与目录不一致）。
  判据本身没问题（`tools/dev/check_governance_surface.py:808`），错在「拿并发中的工作树当基线」。
  修法：补头 → 在**已提交、无并发写入**的树上复跑 `check:quick`（11 gates 绿）与 `check:pr`（20 gates 绿）。
- 未做（pending）：dev 隔离栈上的**坐标级点击**复验（1280/1366 两档）。本单只改了避让规格与
  占位，逻辑面为零，但「遮挡消失」这个结论要由真实布局确认——沿用 issue 的取证脚本形状即可。

## Revisit

- 若批量条文案变长导致 `flex-wrap` 三行（>144px），`h-40` 余量不足——届时应改成
  「由条体测高动态占位」，而不是再加 4 个像素单位。
- 若本仓引入 Playwright（`#703` 系或夜间真机 E2E 顺手做），把这条 bug 的形状钉成**坐标级点击**
  用例（issue 正文的 A/B 对照表就是现成判据），届时静态守卫可以降级为兜底。
- 第三张带批量条的表格页出现时（守卫会红），如果它用的是 `ui/pagination-bar.tsx` 而非内联分页，
  需要确认占位仍在**卡片之外**——守卫只保证「渲染了 `BulkBarSpacer`」，不保证放置点。
