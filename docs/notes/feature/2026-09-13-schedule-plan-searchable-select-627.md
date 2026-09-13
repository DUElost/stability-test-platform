# 定时任务表单 Plan 选择器可搜索化（#627）

Status: implemented
Class: feature

## Decision

定时任务创建/编辑表单的 Plan 原为原生 `<select>`（选项 `<name> (#<id>)`），Plan 数量
上升到数十/上百后只能滚动查找。按 issue 要求改为与 `DeviceMultiSelect` 同形态的
可搜索单选：

- 新增 `frontend/src/components/schedule/PlanSelect.tsx`：文本过滤（**name 或 id**
  子串，均大小写不敏感）、选中后 chip 展示并可单独清除、空态/无匹配/加载三态分离；
- `SchedulesPage` 用 `<PlanSelect id="schedule-plan" plans={plans} …>` 替换原生
  select，继续沿用页面既有的 `plansQ`（`planKeys.list(200)`）。

**数据由父级传入而非组件自取**：`DeviceMultiSelect` 自取数据的模式在这里会让
「组件会否二次请求」依赖 react-query 同 key 去重这一隐式约定，且 loading 语义出现
两个 owner；父级已经把 plans 拉在手里（页面 loading 门控也用它），传 props 更直白。

**可访问性副作用（已固化进测试）**：`<label htmlFor="schedule-plan">Plan 蓝图</label>`
与触发按钮关联后，按钮的可访问名是「Plan 蓝图」而非其内容文本——这对读屏是正确
语义，页面级测试据此按可访问名定位、用 `toHaveTextContent` 断言已选回填。

## Alternatives

- **把 `DeviceMultiSelect` 加 `mode="single"` 复用**：弃——多选与单选的选中态、
  chip 移除语义、回调签名都不同，加模式分支会把一个简单组件变成两个行为的并集，
  且设备组件内部还绑定了设备查询；共享的只有视觉外壳，复制外壳成本更低；
- **组件内自取 plans（同 `planKeys`）**：弃——见 Decision，隐式去重 + 双 loading owner；
- **保留原生 select、加 `datalist` 辅助**：弃——`datalist` 在 select 上不生效，且
  无法满足「选中后 chip + 清空」的验收形态；
- **实现完整 ARIA combobox（键盘上下键/Enter/Esc/焦点圈）**：弃——超出本单；
  当前为「按钮展开 + 输入过滤 + 点击选项」的渐进形态，键盘可 Tab 达，键盘导航列入
  Revisit 而非顺手做（涉及焦点管理，属独立改动）。

## Verification

- **组件测试** `PlanSelect.test.tsx`（6 例）：100 个 Plan 下按名称搜到目标、
  按 ID 搜到目标（含部分数字）、选中回调字符串 id 并收起、chip 清除回调空串、
  「无匹配 Plan」与「暂无 Plan」区分、loading 态；
- **页面级集成** `SchedulesPage.test.tsx`：新建表单 → 按可访问名打开选择器 →
  搜 `87` → 选中 → 断言收起、触发器回填「夜跑计划 87 (#87)」、出现清除按钮；
  - **反例**：临时还原旧 `<select>` 页面 → 该用例
    `1 failed | 3 skipped`（找不到选择器按钮）；恢复后 **10 passed**；
- 前端全量 `npx vitest run` → **763 passed**（103 files）；
- `npm run type-check`、`npx eslint src --max-warnings 0`、
  `python scripts/run_gates.py check:quick`（7 gates）均通过。

未做：真实浏览器内 100 Plan 的 2 秒定位手感（组件级过滤是同步数组过滤，量级无虞；
issue 验收里的「2 秒内定位」由用例语义覆盖，非秒表实测）。

## Revisit

- **键盘导航**：当前无 ArrowUp/Down 高亮、Enter 选中、Esc 收起；若 Plan 选择器被
  其它表单复用或有无障碍要求，应抽成通用 single-select combobox 并补键盘用例；
- **一次拉 200 条上限**：与页面既有行为一致（`api.plans.list(0, 200)`），Plan 超过
  200 条时需要分页搜索（服务端搜索）而非本地过滤；当前规模未触发；
- **编辑态的失效回填**：`selectedId` 由父级 `form.plan_id` 驱动；若将来表单改为
  plan_id 数字型，组件 props 需同步改为 number | null（目前刻意保持 string 以贴合
  现有表单状态，零改动量替换）。
