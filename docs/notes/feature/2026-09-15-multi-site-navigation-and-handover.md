# 多站点 P1/I5：站点导航（`/site/`）与交接证据清单（handover）

Status: implemented
Class: feature

## Decision

落实 [P1 设计](../../design/2026-09-multi-site-installation.md) 的 I5（§3.3/§4/§5 S7/§6）：交付**最小静态导航**与**可复跑的验收证据清单**，两者都只发布获准信息，且不污染共享发布物。

- **导航入口**：新增 `deploy/control-plane/navigation/index.html`（占位符 `<site-id>`/`<site-display-name>`/`<public-url>`/`<site-contact>`/`<documentation-url>`/`<release-version>`/`<rendered-at>`），由 S2 用既有渲染机制渲染到 **`/var/www/stability-site/index.html`**（目录 0755、文件 0644）；nginx 两模板新增只读段 `location /site/ { alias /var/www/stability-site/; }`。
  - 渲染值全部 `html.escape`（显示名/负责人是自由文本），残留 kebab-case 占位符即 fail-closed（HTML 标签名不含短横线，`_UNRESOLVED_NAV_PLACEHOLDER` 精确判据）。
  - **为何不在部署根**：部署根 0750、属服务账号，nginx（www-data）无法穿越；**为何不是前端页面**：`frontend/dist-prod` 是共享发布物，站点差异不应进发布物。
- **`handover` 子命令**（`tools/site_config/handover.py`）：读 `install-state.json`（含新增 `runs` 计数）与可选 `verify --json` 报告，把 MS-01/02/04/05/06/10/13 映射到具体 `check_id`，三态输出（`PASS`/`FAIL`/`BLOCKED` + `pending`），并把脱敏清单写入 `/var/www/stability-site/handover.json`（0644，可经 `/site/handover.json` 下载）。缺证据即 `BLOCKED` 并写明缺什么；有映射检查失败则 `FAIL` 且**不出物**；命中禁止片段（秘密名/凭据）一律拒绝写盘。
- **重跑证据**：安装记录新增 `runs`（`_state_runs()+1`），MS-04 的「重跑不重置/不轮换」由 `runs ≥ 2` + S2 检查支撑，不再靠口头结论。
- **verify 新增 `verify.s6.navigation`**（MS-13 证据）：无认证 GET `/site/`，要求 200 且含站点 id/显示名/负责人/文档链接与 `handover.json` 链接。
- **S4 新增入口收口**：`ensure_frontend_readable()` 只放开目录穿越位（部署根/前端/dist-prod 子目录 0755，文件权限不动），并新增 `install.s4.frontend`（探测 `public_url/` 必得 200）。此前只探测 `/health`，前端不可服务（`GET /` 404）不会被发现。
- **运维文档**：[`site-handover-and-navigation.md`](../../operations/site-handover-and-navigation.md)（导航说明、handover 用法、交接清单含签字位、维护/备份计划模板）并登记两处索引；设计升 v0.7、PRD 升 v0.9。

## Alternatives

- **把站点导航做成平台 UI 页面**：站点差异会进共享发布物（与「同一发布物换输入」冲突）；改为站点侧渲染 + nginx 附加只读段。
- **独立子域/独立 server 块**：需要额外域名与证书绑定，现场成本高；`/site/` 已满足「独立入口、无凭据」。
- **只在运维文档里写验收结论表**：不可复跑、很快与代码/环境脱节；改为 `handover` 从本站产物生成。
- **导航页放进部署根**（`<deploy-root>/navigation/`）：nginx 无法穿越 0750 部署根，实测 404；改为 nginx 可读的 `/var/www/stability-site/`。
- **`alias` + `try_files $uri =404`**：alias 下 `try_files` 会把 URI 再接一遍（`/site/` → `alias/site/`），目录请求 404（实验室实测；显式文件 `/site/index.html` 却 200）；改为只用 alias，index 由 server 级继承。
- **把「前端可服务」并入 `/health`**：`/health` 是反代到 :8000 的应用探针，掩盖静态根权限问题；改为独立入口探测。

## Verification

- **仓库离线**（worktree，base=origin/main `fd3dcd9e`）：`pytest tests/ -q` → **719 passed**（新增 `tests/test_site_handover.py` 19、`test_site_agents.py` 导航用例 2、`test_site_install.py` 入口负例与导航/`runs` 断言）；`ruff check backend/ tools/ scripts/` 通过；`scripts/run_gates.py check:quick` **10 gates OK**；`tools/verify_control_plane_templates.py` OK（新增 `/site/` 段与导航模板占位符集合不变量）；`tools/dev/check-internal-ip-leak.py --check` 通过；`git diff --check` clean。
- **238 容器实验室**（复用 I4 的 `br-stp` + 控制面容器 + 2 Agent；bundle 由工作树重组）：
  - 幂等重跑 `install` PASS（S0–S4，含 `install.s2.navigation navigation_rendered`、`install.s4.frontend frontend_served`）；`state.runs` 由 2 → **3**（重跑证据）。
  - `GET /site/` → **200**，页面含站点 ID/负责人 `lab-ops`/文档链接/`handover.json` 链接，且不含 `/opt/stp-control`、`AGENT_SECRET`、`PRIVATE`、`password`（逐项核对 clean）。
  - `verify` **RC=0**：auth/csrf/hosts/**navigation**(`navigation_published`)/devices/chain PASS；watcher/storage/scan_upload_merge 如实 BLOCKED。
  - `handover` **RC=0**：MS-02/04/05/06/10/13 **PASS**，MS-01 **BLOCKED**（非原作者复跑与真机主链未做，`pending` 已列明）；`/site/handover.json` → 200；`runs: 3`、`verification: {provided: true, status: PASS}`。
  - 负例（MS-13 语义）：移走 `index.html` → `/site/` **403**、`GET /` 仍 **200**；恢复后 `/site/` 200。
- **实验室暴露并修复的缺陷**：① 部署根 0750 → nginx 无法穿越 → `GET /` 404（此前 I3/I4 只验 `/health` 与 `/api`，前端不可服务未被发现）；② `alias` + `try_files` 目录请求 404。③ 实验室脚本自身的 bundle 清单用了过期迁移 head（S3 `release_schema` FAIL）——属实验装置，不是产品缺陷，已改由工作树现算 head。
- **pending（现场）**：MS-01 的非原作者复跑与真机 S6、真实存储写读、scan/upload/merge、浏览器点击核对（导航不可用 → 原入口仍可登录）、城市 C 的跨站点隔离证据与签字。

## Revisit

- **handover 只读最近一次安装记录**：部分重跑（例如不带 `--through-agents`）会覆盖 `install-state.json` 并使 S5 相关证据消失（本次实验室即出现：MS-01 因缺 `install.s5` 转 BLOCKED）。现场应按「完整安装（含 `--through-agents`）→ `verify` → `handover`」顺序执行；若需要历史留档，应把每次安装报告按运行时间落盘（后续切片）。
- **`/site/` 无认证**：设计允许「导航无凭据」，当前两 profile 都启用；若现场安全要求收窄（例如只对内网开放），按站点输入加开关。
- **部署根穿越位**：现在 S4 明确把部署根/前端目录设为 0755（文件权限不变，`.env.backend` 仍 0600）。若未来把控制面改由专用 web 账号运行，应收窄回 0750 并让 nginx 以该账号运行。
- **`install.s4.frontend` 探测的是公开入口**：CP 无法解析自身公开 URL 的站点会 FAIL——这是有意的（入口不可达本身就不算装好），但现场 DNS 未就绪时需先修 DNS 再重跑。
