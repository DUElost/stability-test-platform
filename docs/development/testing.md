# 测试指南

> **最后更新**：2026-09-05
> 本文是测试命令、隔离数据库要求、fixture 陷阱和已知限制的权威位置。

---

## 1. 目录与边界

| 目录 | 范围 | 依赖 |
|------|------|------|
| `backend/agent/tests/` | Agent、pipeline、watcher | **无 PG**，日常优先 |
| `backend/tests/` | 控制面 API / 服务 / 集成 | PostgreSQL（testcontainers 或显式 URL） |
| `frontend/**/*.test.tsx` | 组件 / 页面 | vitest + jsdom |
| `tests/`（根） | 脚本、Ansible、迁移契约 | 按文件而定 |

**不要混跑** agent 与控制面 fixture。一律 `python -m pytest`（裸 `pytest` 可能落到错误解释器）。

包装脚本（可选加载 `.env.test`）：

```bash
cp .env.test.example .env.test   # 首次
./scripts/run_pytest.sh backend/agent/tests/ -q
```

根目录 `pytest.ini`：`pythonpath=.`、`asyncio_mode=auto`。

---

## 2. 生产机 / 本机业务库约束

部分部署机上 **本机 PostgreSQL 即生产库**。在此类主机改码时：

| 场景 | 做法 |
|------|------|
| 日常验证 | 优先 `pytest backend/agent/tests/` |
| 必须跑 `backend/tests/` | **Docker testcontainers**（`conftest` 在未设 `TEST_DATABASE_URL` 时拉起临时 `postgres:16`） |
| 迁移试验 | 禁止对业务库试跑 `alembic upgrade`；在 CI / 容器 / 开发机验证 |
| ❌ 禁止 | `TEST_DATABASE_URL=...@localhost:5432/<业务库>` |

**无 SQLite 退路**：`conftest` 固定拉起 testcontainers Postgres（`test_ci_and_test_harness_files.py` 契约钉住不得存在 SQLite 回退路径）。

用户须在 `docker` 组（`permission denied` 时 `usermod -aG docker` 后重新登录），不要用生产 `DATABASE_URL` 代替测试库。

### 测试容器与残留巡检（#1482）

- 未设 `TEST_DATABASE_URL` → conftest **每进程起独立 `postgres:16` 容器**
  （注意：`DATABASE_URL` 会被 conftest 覆盖，想固定库必须设
  `TEST_DATABASE_URL`，且受 #1300 命名护栏约束）；
- 被 kill/超时的 pytest 进程会**遗留容器**（实测存量 36 个、最老 >2 周）；
- 残留有**两种形态**：仍在运行的孤儿容器；以及宿主/daemon 重启后被批量优雅
  停掉（exit 0）却无人回收的**已停容器**——巡检两种都覆盖（列举带 `-a`：#1936）；
- **进程内兜底（#1492）**：conftest 在正常结束（`sessionfinish`）、
  `SIGTERM`/`SIGINT`、`atexit` 路径主动停掉**本进程**的容器（幂等、
  best-effort，信号路径保持原退出语义）；**`SIGKILL` 无法拦截**，由下方
  巡检兜底；
- 巡检（只读）：

  ```bash
  python tools/dev/check_test_containers.py            # dry-run 报告 + 建议命令
  python tools/dev/check_test_containers.py --strict   # 有残留时退出码 1
  ```

- 清理（**默认阈值 120 分钟**，只动疑似残留、不碰其他会话活跃实例；目标以
  testcontainers 注入的 label 判定，手工起的同名 PG 不会被误删）：

  ```bash
  python tools/dev/check_test_containers.py --prune          # 列出待删清单（不删）
  python tools/dev/check_test_containers.py --prune --yes    # 确认后执行删除
  ```

生产唯一 env 源是仓库根 `.env.backend`；`backend/.env` 是本地开发覆盖，不含生产
`DATABASE_URL`。数据库代码通过 `backend/core/env_source.resolve_database_url`
解析配置，没有生产连接串兜底。测试不得读取或复用 `.env.backend`。

---

## 3. 后端测试环境

| 变量 | 说明 |
|------|------|
| `TESTING=1` | conftest 设置；禁用 Redis/SAQ/Scheduler lifespan |
| `TEST_DATABASE_URL` | 仅隔离库；生产机请 **unset** 走 testcontainers。显式地址有机器护栏（#1300）：库名必须含 `test`（如 `stp_test`）且不得与运行时 `DATABASE_URL` 相同，违者 conftest 拒绝启动；确需豁免设 `STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1`（记 warning） |
| `JWT_SECRET_KEY` | 必设（见 `.env.test.example`） |

```bash
unset TEST_DATABASE_URL
JWT_SECRET_KEY=test-secret python -m pytest backend/tests/path/to/test.py -q
```

协议 / abort / 链相关用例映射见 [`../design/07-execution-protocol.md`](../design/07-execution-protocol.md) §8。

执行协议 migration 前：

```bash
python -m backend.scripts.migration.preflight_execution_protocol
```

---

## 4. 前端测试

```bash
cd frontend
npx vitest run
npx tsc --noEmit
npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx
```

- `@/` → `src/`  
- PlanRun capabilities（如 `final_archive`）由后端权威控制；测试须显式 mock，勿依赖「缺省为 true」。  
- Watcher 信号防抖 2s：断言 refetch 用 `waitFor({ timeout: 4000 })`。

### jsdom 的边界（#2700）：几何/命中/浏览器语义在此**结构上不可测**

vitest 跑在 jsdom 上（`frontend/vitest.config.ts`），而 jsdom **没有布局引擎**：
`getBoundingClientRect()` 恒全 0、`elementFromPoint()` 无意义、滚动位置与
`position: fixed` 的覆盖关系不存在，autofill/下载/`document.title` 也不在其中（实测
`src/**/*.test.tsx` 里 0 个文件引用 `getBoundingClientRect`/`elementFromPoint`/
`offsetParent` 三者）。因此——

- **「我加了 jsdom 用例」不等于守住了遮挡/命中类回归**：这类结论依赖「屏幕上谁压住谁」，
  在此维度上写多少用例都是 0 覆盖；
- 此类回归由**静态守卫**（首选，秒级、离线）或**真实浏览器**承担；
- 静态守卫的范式已有先例：`tests/test_frontend_bulk_selection_guard_2614.py`（导入图配对
  断言「有悬浮批量条的页面，其可选中表格必须渲染共享占位」+ 几何字面量锁在单一来源），
  与 `tests/test_admin_only_read_surface_register.py` 同形。**几何约定大多可静态化**：
  谁必须渲染占位、某类容器不得同时出现两个 fixed 覆盖层、z-index 分层表；
- 静态守卫的**天花板**要知道：它能证明「占位存在且在卡片外」，证不了「`h-40`(160px) 在
  任何视口都够」——余量是实测取的（#2614 按条体 ≈63px 单行 / ≈110px 两行），换视口后
  仍可能不够。

**已发生的 5 例（浏览器层若建，锚点用例直接取此表，不要为覆盖率另造）**：

| 单 | 缺陷 | jsdom 为何测不到 |
|---|---|---|
| #2614 | 全选后固定底部批量条压住分页控件，坐标点击被吞 | 纯覆盖层几何；实现方的用例注释自陈「jsdom 没有布局引擎，测不了命中测试」 |
| #2453 | 密码管理器 autofill 后建不出用户 | autofill 不派发 React 的 change 事件，是浏览器行为 |
| #2363 | 部分路由 `document.title` 不更新 | 标题是浏览器表面 |
| #2028 | PlanRun 日志页 CSV 导出（blob 下载）零测试 | 下载与 blob 语义 |
| #1708 | 项目编辑「改字段+改 key」并发双请求，字段静默丢失 | 需要真实网络时序与浏览器并发语义 |

层与 #169（夜间真实设备 E2E）**不同**：那条覆盖 ADB/文件系统/硬件，不含浏览器 UI 层。

---

## 5. CI（`.github/workflows/ci.yml`）

1. `compileall backend/`  
2. `pytest backend/tests/`（PostgreSQL service）  
3. `npx vitest run` · `tsc --noEmit` · `npm run build`  
4. Docker build（依赖前序 success）

---

## 6. 验收文档

| 文档 | 用途 |
|------|------|
| [`../acceptance/00-platform-smoke.md`](../acceptance/00-platform-smoke.md) | 平台级 AC |
| [`../acceptance/2026-plan-c-sprint2-3.md`](../acceptance/2026-plan-c-sprint2-3.md) | 方案 C |
| [`../preprod-drill-runbook.md`](../preprod-drill-runbook.md) | 手工发版 |

---

## 7. 编写约定

- 新 API → `backend/tests/api/`  
- 新 Agent 行为 → `backend/agent/tests/`  
- 新 UI → Vitest；主机表见 `ExpandableHostTable.test.tsx`  
- 主链 → `integration/` + 更新 `07-execution-protocol` / `01-execution-pipeline` 若契约变化  

### 等异步状态：只用「可观测条件 + 上界」，不用固定 `time.sleep`（#2595 / #2602）

固定等待只有两种合法形态，其余都是缺陷——判据是**意图**：

- **(a) 等某个状态出现** → 写成有界轮询：
  `while not <可观测条件> and time.monotonic() < deadline: time.sleep(0.005)`，
  超时即 `pytest.fail` / `assert` 并带上「在等什么」。可观测条件可以是计数器
  （`u.stats.submits_dropped`）、公开状态（`s.waiting_devices`、`run.status`）、
  事件对象（`entered.wait(timeout=…)`）。
- **(b) 场景搭建**——睡眠本身是被测语义的一部分（制造"慢段"让周期心跳发生、占住 permit
  让后来者排队、放大交错窗口让无锁实现必交叉），必须在注释里写明**为什么没有可等的量**。
- **否定断言**（"之后再没有 X"）单独注意：非事件没有正向可等的量 → 先等前提成立
  （"已处理完"、"线程已退出"）再断言，否则按 (b) 保留并说明。

为什么值得守：这个套件里裸等待造成的随机红**只在特定 job（夜间 / PR 路径）暴露**，
平均晚一天被发现（#2551）；而等量算错还会造成更隐蔽的**"静默没测到"**——断言依赖的状态
未被等待时，用例看似通过却没测到要测的东西（实例：`sleep(0.1)` 后断言唤醒延迟，线程若
还没起来延迟恒 ≈0）。

2026-09-17 按该判据扫过两个套件（`backend/tests/` 259 个用例文件、`backend/agent/tests/`
162 个）：改 19 处、留 11 处（各带定性依据）。负向形态与逐条分类见
`docs/notes/bug-fix/2026-09-17-*bounded-waits*.md` 与 `2026-09-17-*wait*` 系列 Note。

### 源扫描型守卫：先证锚点在，再判形态（#2639）

「读被测模块源码文本 + 断言某字面量在/不在其中」的守卫，其有效性完全依赖锚点与被扫对象
仍然重合，而这件事默认没人检查。三种失败形态里**只有前两种会响**：

1. 被扫逻辑搬走 → `import` 失败 → collection error（响亮）；
2. 正向断言的字面量搬走 → 恒红（响亮）；
3. **否定断言恒真** → 守卫还在跑、还是绿的，但它守的是一份已经没有那些代码的文件
   （实例：DLE 落库点随 #1520 从 `agent_api` 搬到 `agent_device_log_events` 后，
   `assert "row.state = ev.state" not in src` 恒真了一个完整窗口）。

写法则用 `tools/dev.source_anchor.SourceGuard`，把「取源码」与「证明锚点在场」绑成一个
不可拆开的动作，三类红在**消息第一行**即可区分：

```python
guard = (
    SourceGuard.of_module(agent_device_log_events)          # 或 of_repo_path("…/x.py")
    .anchored("resolve_initial_upload_state(ev.event_type, ev.state)")  # 不在 ⇒ 用例已过期
)
guard.assert_count("state=resolve_initial_upload_state(ev.event_type, ev.state)", 2)
guard.assert_absent("row.state = ev.state", why="#2025 裸赋值不得回潮")   # 出现 ⇒ 防线回归
```

- `anchored()` 只证明「逻辑还在这个文件里」，**不是**被测行为；被测行为仍由 `assert_*` 表达；
- `assert_*` 必须先有锚点，否则 `GuardMisuse`（把恒真断言的入口封死）；`assert_absent` 的
  `why` 必填——写不清防的是谁，就说明这条断言不该存在；
- 计数也是锚点的一部分：只判「≥1」会放过「被复制/部分搬走」，需要时用 `anchored(…, expect=n)`。

存量不要求一次改完：`tests/test_source_scan_anchor_ratchet.py` 按 AST 认「读源码 + 否定断言」
的用例（注释里的同形文本不算，承接 #2641/#2642），新写的必须走助手，基线只能缩短。
该棘轮放在根 `tests/`（required check 路径）而不是 `backend/tests/`（只在夜间全量跑）——
否则它自己就成了「红只有夜间可见」的那一类。

顺带一条同源教训：#2639 用 `git grep … -- 'tests/**/*.py'` 数这族用例时，git pathspec **不认**
未加 `:(glob)` 的 `**`，`tests/` 整棵树静默零命中（81 个文件没进统计）。任何「扫不到就算通过」
的判据都要自带非空断言——本节的棘轮因此显式断言「一个 offender 都扫不到即红」。

## 8. 已知限制

- 真机 ADB/NFS 不在默认 CI  
- 控制面全量可能较慢；可按文件跑 `-x`  
- E2E dedup extract 需共享存储环境  
- **mock `subprocess.Popen` 必须补全 `stdout`/`stderr`（#123）**：`pipeline_engine._pump_process` 用 reader 线程逐行读流；未配置的 `MagicMock` 流会让 `readline()` 永不返回空串、reader 无限 append，内存以数百 MB/s 增长直至 OOM（曾导致整机冻结）。写法：`proc.stdout = io.StringIO(""); proc.stderr = io.StringIO("")`。整目录验证建议套内存上限：`systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -- venv/bin/python -m pytest backend/agent/tests/ -q`

## 9. 真机清理三态回归（夹具，#2162）

teardown 类脚本（`monkey_teardown` / `gpu_finish` / `powercycle_finish` / `sleep_finish`…）的
「清理 + 回读验证」必须覆盖三态：**① 删除成功 / ② 残留转红 / ③ 探测不可用转红**。真机不在
默认 CI，用夹具一键回归：

```bash
# 在 agent 宿主机上（该机有 adb，且脚本已部署到 /opt/stability-test-agent/agent/scripts）
python3 tools/dev/teardown_cleanup_states.py --serial <SERIAL> --script monkey_teardown
python3 tools/dev/teardown_cleanup_states.py --serial <SERIAL> --script gpu_finish --json

# 从控制面经 ansible 下发执行
ansible -i ~/hosts.ini <host> -m copy -a 'src=tools/dev/teardown_cleanup_states.py dest=/tmp/ mode=0644'
ansible -i ~/hosts.ini <host> -m shell -a 'python3 /tmp/teardown_cleanup_states.py --serial <SERIAL> --script monkey_teardown'
```

退出码：`0` 全通过；`1` 有用例失败（**实现**行为不符）；`2` 夹具错误（环境/前置自检不满足）。

两条设计规则（都来自真机踩坑）：

- **构造必须消除竞态**：② 用设备端无间隔紧凑循环重建目标；带 `sleep` 的循环会与探测形成
  时序空档，把「实现正确」误判成失败（2026-09-15 真机验证时 A2/B2 各假阴性一次）。
- **③ 用真实 adb**：把探测那一跳指向不存在的 serial（真实 rc≠0），不 stub 探测输出——
  stub 只能验证测试自己的想象。
- 前置自检（循环存活、目标确实被重建）不过 → 报**夹具错误**而非用例失败；设备上有
  monkey/aim 相关进程时默认拒跑（`--force` 越过）。
