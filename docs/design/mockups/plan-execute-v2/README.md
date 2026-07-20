# Plan Execute V2 — 静态预览

视觉基准，供实现对照。**实现方案**见：

[`../../2026-07-plan-execute-page-improvements.md`](../../2026-07-plan-execute-page-improvements.md) **§8**

| 文件 | 对应冲刺 |
|------|----------|
| `index.html` | 索引与设计结论 |
| `00-plan-select.html` | 态 0 · Plan |
| `01-workspace-matrix.html` | 冲刺 A · 矩阵主视图（可交互） |
| `02-workspace-table.html` | 冲刺 A · 表格辅视图 |
| `03-dispatch-cockpit.html` | 冲刺 B + A+ · 发起驾驶舱 |
| `styles.css` | 预览令牌（实现时映射到 App design-system，勿整文件拷贝） |

本地预览：在目录下起任意静态服务器，例如 `python3 -m http.server 18901`。
