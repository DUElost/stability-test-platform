# 站点导航与交接（多站点 P1 / S7 / I5）

适用：**独立站点**（多城市部署）的控制面。站点装好后，运维需要一条不依赖平台登录的入口来确认「这是哪个站点、谁负责、文档在哪、这版是什么」，并在交接时给出**可复跑的验收证据**。本文是这两件事的权威说明。

## 1. 站点导航入口 `/site/`

- 由站点安装（`tools.site_config install` 的 S2）从 `site.yaml` 的 `navigation.contact` / `navigation.documentation_url` 与站点身份、发布版本渲染，落在 **`/var/www/stability-site/index.html`**（目录 0755、文件 0644）。
- Nginx 以只读静态段提供：`location /site/ { alias /var/www/stability-site/; }`（两个模板都有）。
- **为什么不在部署根里**：部署根是 0750、属服务账号，nginx（www-data）无法穿越；导航页是站点侧公开物，放独立可读目录。
- **为什么不是前端页面**：前端 `dist-prod` 是**共享发布物**，站点差异不应进发布物；`/site/` 由站点输入渲染，换站点只换输入。
- 页面只发布：站点 ID / 显示名、平台入口、负责人、运维文档链接、发布版本、本页生成时间，以及 `handover.json` 的链接。**不含**凭据、秘密名、内部地址与路径（模板不变量由 `tools/verify_control_plane_templates.py` 守）。
- 页面不写脚本、不引外部资源；**本页不可用不影响平台**：各站点原入口仍可独立登录运行（PRD MS-13）。

## 2. 交接与验收证据 `handover.json`

```bash
# 在站点控制面机内执行（先装好站点，再跑过 verify）
python -B -m tools.site_config handover \
  --config /absolute/path/site.yaml \
  --state-dir /protected/state \
  [--verify-report /protected/verify.json] [--dry-run] [--json]
```

- **先完整安装、再 verify、最后 handover**：安装记录只保留最近一次运行，部分重跑（例如不带 `--through-agents`）会覆盖它并使 S5 相关证据消失。
- 读安装记录 `install-state.json`（**含 `runs` 运行次数**）与可选的 `verify --json` 报告，把 PRD 的 P1 验收条目映射到具体 `check_id`：
  - `MS-01` 空白站点装完并按指南接入（S0–S4 + S5 + verify Host）
  - `MS-02` 同一发布物换输入复用、无跨站点泄入（摘要/站点级秘密/绑定）
  - `MS-04` 重跑不重置不轮换（要求 `runs ≥ 2`）、破坏性步骤前失败
  - `MS-05` 依赖与制品只来自声明介质（受控镜像 / 离线 wheelhouse）
  - `MS-06` 登录/CSRF、Host/设备、Plan→claim→租约→终态（verify 链）
  - `MS-10` 无占位弱口令、SSH 校验不放宽、入口可辨识
  - `MS-13` 站点与入口可辨识、导航无凭据、原入口独立可用
- 每条的状态只有三态：`PASS`（映射到的检查在本次产物中全绿）、`FAIL`（有映射检查失败 → 不出物）、`BLOCKED`（**缺证据**，在 `pending` 里写清缺什么）；文件落 `/var/www/stability-site/handover.json`（0644，可经 `/site/handover.json` 下载）。
- **证据 ≠ 签字**：`handover.json` 是当前可复跑证据的快照；真机 S6、存储写读、scan/upload/merge、浏览器点击核对等在现场完成后才会从 `BLOCKED` 转正。

## 3. 交接清单（签字位）

逐项确认后由安装人与站点验收人签字；未完成项必须写进「未覆盖项」列。

| 项 | 判据 | 证据 | 安装人 | 验收人 | 日期 |
|----|------|------|--------|--------|------|
| 站点装完 | `install` S0–S4 全 PASS | `install-state.json` + 安装报告 | | | |
| Agent 接入 | S5 双 Host `digest_matched`/`endpoint_recorded` | 安装报告 S5 段 | | | |
| 受控验收 | `verify` RC=0（`chain_completed`） | `verify.json` | | | |
| 导航入口 | `/site/` 200，页面仅含获准信息 | 浏览器 + `handover.json` | | | |
| 原入口独立可用 | 导航不可用时平台仍可登录 | 浏览器（去掉 `/site/` 内容后再试） | | | |
| **未覆盖项** | 逐条抄 `handover.json` 的 `pending` | — | | | |

## 4. 维护与备份计划（模板，按站点填写）

- **备份**：控制面数据库每日全量 + WAL 归档；`.env.backend`（0600）与绑定文件（0700/0600）随秘密保管流程单独备份——**放入备份不等于可重建**，恢复演练见 P2（本站点 P1 不含升级/恢复演练）。
- **升级**：Agent 走既有热更新与升级门禁（见 [`agent-version-and-hot-update.md`](./agent-version-and-hot-update.md)）；控制面发布物更新与站点重跑语义见设计 §4（重跑不轮换秘密、不重置身份）。
- **巡检**：`/health`、`/site/`、`handover.json` 的 `pending` 列表随维护窗口复核；`runs` 增长本身是重跑证据，但**重跑前必须确认当前发布物身份**（清单摘要）。
- **联系人**：站点输入里的 `navigation.contact` 即当班负责人；变更后重跑安装以刷新导航页。

## 5. 边界

- 本页与 `handover.json` 都**无认证**：只放获准公开的信息；任何凭据、绑定名、内部地址、磁盘路径都不得写入。
- `handover` 不替代现场验收：它把「已有证据」映射清楚，并对缺证据的条目保持 `BLOCKED`。
- P1 完成不代表 P2 升级/恢复演练、P4 只读总览已交付（设计 §5 S7 行）。
