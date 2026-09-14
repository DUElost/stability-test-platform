# 控制面进程级内存采集与健康页展示（宿主机内存采样器）

Status: implemented
Class: feature

## Decision

以「node_exporter textfile 采集器 + 后端代理 Prometheus + 现有健康页」三段式落地
进程级内存观测，不引入新的时序存储与前端数据通道：

1. **采集**（新增文件）：`deploy/control-plane/node-exporter/stp-mem-top.sh`，
   由 `deploy/control-plane/systemd/stp-mem-top.{service,timer}` 每 2 分钟执行，
   扫描 `/proc/*/smaps_rollup` 的 `Anonymous` 值，按 `comm + cgroup unit` 聚合
   后写 node_exporter textfile：
   - `stp_hostproc_anon_bytes{comm,unit}`：Top-10 进程组。标签刻意不用 pid
     （Top-N 每轮变化会制造大量短命序列），`comm` 取 argv0 基名（node 进程的
     `comm` 常被线程名占成 `MainThread`），`unit` 直接指向 systemd cgroup
     scope —— AI 会话/终端窗口据此可定位；
   - `stp_hostproc_anon_total_bytes`：全机进程匿名内存合计；
   - 另追加式写出 `/var/log/stp-mem-top.tsv`（含 pid 与完整命令行，供人工回溯）。
2. **后端**：`backend/services/file_server_monitor.py` 新增
   `_PrometheusClient.vector`（多序列 instant query——`scalar` 只取首样本，
   不适用于 `topk`）与 `_top_process_items`；控制面板新增 `processes`
   （`available/error/items`），`history` 新增 `hostproc_total_anon_bytes`
   （趋势固定取控制面 job，与存储面板是否分源无关）。
3. **前端**：`FileServerPage` 控制平面 tab 新增「进程内存 Top 10」表 + 合计趋势
   折线；采集器未部署时降级为 InlineEmpty 提示而非报错。

指标使用 node_exporter 原生前缀（非 `stability_*`），不进注册表，因此不触发
`tests/metrics_registry.py` 与 Grafana/告警契约门禁；采集器缺失时全链路不受影响
（`available=false`）。

## Alternatives

- **走 agent 心跳 `collect_system_stats()` → `host.extra`**：只能给最新快照、
  无历史、无 per-process，且控制面自身不跑 agent（该通道面向测试机）；否决。
- **后端在 `/metrics` 里直接输出 per-process gauge**：把宿主机探针耦合进业务
  进程，序列随 pid/时间抖动，还要动 `stability_*` 注册表契约；否决。
- **引入 cAdvisor / process-exporter**：为单一诉求新增守护进程与抓取目标，
  收益不抵运维面；textfile 模式本机已有先例（nvme / apt / smartmon）。
- **标签带 pid 提供精确定位**：序列抖动问题；pid 与完整命令行留在 TSV 日志，
  指标层只用稳定标签。

## Verification

- 本机实测（2026-09-14）：采集器部署后 `stp_hostproc_anon_bytes` 10 条序列进入
  Prometheus（`topk(3, …)` 返回真实排名）；脚本单次执行约 1.4s、service
  `status=0/SUCCESS`、textfile 落盘为原子替换（0644）。
- 后端：`backend/tests/services/test_file_server_monitor.py` 18 passed（新增 3 例：
  Top-10 排序、缺采集器降级、查询失败 error 上报）；
  `backend/tests/api/test_stats.py::TestFileServerOverview` 3 passed（schema 契约）。
- 前端：`FileServerPage.test.tsx` 7 passed（新增 2 例：面板渲染 + 降级提示）。
- 门禁：`python scripts/run_gates.py check:quick`（结果见 PR）。

## Revisit

- 宿主机 swap/PSI 告警规则当前是**本机草案**（`/etc/prometheus/rules/host-memory-draft.yml`）：
  按 ADR-0011，告警扩展需后续独立 ADR 后才能进 `deploy/prometheus/`（该目录本期
  在 #743 在窗执行范围内，暂避让）。
- 趋势目前只画「合计」一条线；若要「单个进程组的历史曲线」，可对当前 Top-1 的
  `(comm, unit)` 追加一条 range 查询（本期有意不做，避免前端多曲线的标签管理）。
- 存储面板（分源场景）无进程内存；若存储机也部署同一采集器，复用同一查询换 job 即可。
