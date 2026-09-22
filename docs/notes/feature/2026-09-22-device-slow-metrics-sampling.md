# 设备慢指标低频采样（在线态保持每拍，电量/温度/版本/延迟按 serial due 门控）

Status: implemented
Class: feature

## Decision

/devices 页的关键信号是 **`adb devices` 在线状态**，但它此前与慢指标共用同一条
每拍全量采集链：`collect_device_info` 对每台在线设备每拍跑 echo(5s) + dumpsys
battery(10s) + getprop(5s) + **ping -c 3（最坏 15s×2）**——慢探测（尤其 ping 超
时）拖长 heartbeat tick，在线状态的上报被排在它们后面。本单把慢指标降频：

- `collect_device_info` 新增 `include_metrics` 开关：`False` 只做 echo + 平台
  缓存探测，慢三件套整体短路；默认 `True` 保持原行为（消费方兼容）。
- `HeartbeatThread` 对 {battery_level, temperature, network_latency,
  build_display_id} 建 **per-serial due 门控 + 缓存**：非 due 拍带缓存值上送
  （控制面 `_update_if_not_none` 幂等，与 #2757 disk 同形），每拍 adb 调用从
  4+/台 降到 1 次 echo/台。
- **强制采样（不等窗口）三场景**（owner 裁决提出）：
  1. 新设备接入：无缓存＝首见 → 首拍立即采，页面不空一个窗口；
  2. 断线后自动恢复：上一拍连通为 False → 回线首拍强制重采——断电期间电量温度
     真实变了，旧缓存会误导；
  3. 开关机专项测试：设备每次重启回线都落在场景 2 的边上沿判定里，天然逐回归
     新鲜；全失败拍（echo 通但三项探测均无值，如重启后服务未稳）不写缓存不设
     窗口，下一拍继续强采。
- 新旋钮 `STP_DEVICE_INFO_SAMPLE_INTERVAL_SECONDS` 默认 **1800s（30 分钟）**
  （owner 裁决：慢指标可低频到半小时、接受滞后 30 分钟）。`0/负 = 不节流（回退
  每拍采集）`——与 disk 的「0=关闭」语义刻意相反：慢指标是页面必采项，不存在
  关闭档；settings 读不到（#2279）同方向退化。
- 存储不在本单：`df /data` 已被 #2757 节流为 300s + 缓存，本次未动。
- 写库侧 30s `_should_write_hardware_snapshot` 与 WS materiality 均不感知本次
  变化（payload 键形状不变，值变新鲜度）。

## Alternatives

- **每拍照采、只调快 `POLL_INTERVAL`**：tick 时长被 ping 超时钳住，缩短节拍只会
  让更多拍在探测里超时重叠，在线状态反而更堵；降的是采集量不是节拍。拒绝。
- **busy 主机逐拍、idle 主机降频**（以 active job 数门控）：看似贴合场景 3，但
  恰好在最拥塞的主机上保留全量慢探测——把减负目标反置；场景 3 已由「回线强采」
  覆盖（开关机重启必然断线重连）。拒绝。
- **disk 同款全局 monotonic 门控**：一台新设备最坏空 30 分钟才出现电量，场景 1
  不可接受；per-serial 门控顺带把 fleet 采样峰摊平。拒绝全局形态、保留其缓存上
  送模式。

## Verification

- 定向：`pytest backend/agent/tests/test_device_slow_metrics_sampling.py
  test_device_discovery.py test_device_disk_observability_2757.py
  test_heartbeat_parallel_probe.py test_heartbeat_thread_device_error.py
  test_agent_settings_heartbeat.py test_heartbeat_thread_adb_server_conflict.py`
  → **122 passed**（含三场景端到端节拍断言 `[T,F,F,T,F,T]` 与回退矩阵）。
- 全量：`pytest backend/agent/tests` → **2267 passed**。
- `python scripts/run_gates.py check:quick` → 14 门禁全绿（含 env-inventory：
  新旋钮已登记 `backend/agent/.env.example` + `environment-variables.md`）。
- 未验证（诚实标注）：真机 tick 时长收益需热更新后看 fleet 日志
  `device_slow_metrics_sampled devices=n/N window=1800.0s` 与实际节拍；
  新测试桩签名改动仅限既有 fake 的 kwargs 兼容，未触碰控制面。

## Revisit

- 部署后若出现「测试窗口内希望电量曲线更密」的诉求，把旋钮调小即可（如 60s），
  不需改码；若要求按机型/项目差异化，再评估 per-host 覆盖。
- disk 的 300s 全局门控同样存在「新设备首见等待窗口」问题（形态比本单轻），
  统一为 per-serial due 是后续候选。
- build_display_id 归入慢指标后，刷机完成的版本更新依赖「设备重启→回线强采」
  链路；若出现不重启即换版本的场景（热升级分区），需把 build 从慢组摘出。
