# Tailwind v4 主题映射丢失 hsl() 包装 → 全站颜色工具类失效

Status: implemented
Class: bug-fix

## Decision

majors 迁移（Tailwind 3 → 4，#252）把颜色映射写成
`@theme inline { --color-primary: var(--primary); }`，而 `:root` 里的
`--primary` 存的是裸 HSL 通道值（`217 91% 60%`，shadcn v3 风格）。Tailwind 4
的 inline 语义是把该值直接内联进工具类，于是产物变成
`.bg-primary{background-color:var(--primary)}`，运行时代入为
`background-color:217 91% 60%` —— 非法颜色，被浏览器整条丢弃。

结果：所有 `bg-*` / `text-*` / `border-*` 颜色工具类失效，页面只剩黑白与
透明背景，字体（`@font-face` 不受影响）和部分显式 `hsl(var(--x))` 用法
（滚动条、图表）仍正常。这与旧 Tailwind 3 配置里 `hsl(var(--primary))`
的包装层缺失完全一致。

修复：`@theme inline` 中 27 个颜色映射统一改为
`--color-x: hsl(var(--x))`（radius / font 映射不受影响）。构建产物恢复为
`background-color:hsl(var(--primary))`。

## Alternatives

- 把 `:root` token 改成完整颜色（如 `--primary: hsl(217 91% 60%)`）：
  需同步 `.dark`、chart 内联 `hsl(var(--x))` 用法与 `opacity` 修饰符
  （`/50` 依赖 `color-mix` + 通道值），改动面更大。
- 放弃 `inline`：会多一层 `var(--color-x)` 间接引用，但仍需 `hsl()`，
  无收益。

## Verification

- 构建产物 grep：`.bg-primary{background-color:hsl(var(--primary))}`、
  `.text-primary{color:hsl(var(--primary))}`
- 无头浏览器（puppeteer-core + Firefox BiDi）计算样式：登录按钮
  `rgb(60,131,246)` 底 / 白字、链接同色、容器浅灰底
- `scripts/run_gates.py check:quick` 全绿

## Revisit

CI 没有「构建产物视觉/计算样式」断言，此类 CSS 语义回归只能靠生产构建后
发现。若后续再加主题色，给 majors 类变更配一条「headless 截屏 + 关键元素
计算样式」的 smoke（可考虑接入 vitest 的 computed-style 断言）。
