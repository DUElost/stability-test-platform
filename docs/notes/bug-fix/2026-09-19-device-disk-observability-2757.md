# #2757 设备 disk_total/disk_used 心跳覆盖率 0：agent 侧低频采样 + 上报链接通

Status: implemented
Class: bug-fix

## Decision

`device.disk_total / disk_used` 自 #1356 起就是 Device 模型与 DeviceOut 字段，控制面
`heartbeat.py:482-483` 也一直在用 `_update_if_not_none` 消费——但 agent 侧从未采集，
589 台在线设备覆盖率恒 0（issue 实测：391 MTK + 全部 UNISOC 均 NULL）。

补 agent 侧链路，采集频率与心跳频率解耦：

1. **采集原语**（`device_discovery.collect_device_disk`）：单台 `adb -s <serial>
   shell df /data`（timeout=10s）；`parse_df_data` 纯函数解析三种真实形态——
   toybox 两行表（人类后缀）、busybox 两行表（表头 `1K-blocks`，裸数字 ×1024）、
   toybox 单行（`/data: used avail pct`，total=used+avail）。**判据保守**：
   解析不出返回 `(None, None)`，不猜容量——覆盖率宁可空、不可假。静态设备
   （dev 夹具 `STP_STATIC_DEVICE_SERIALS`）直接返回 None，不伪造。
2. **时间门控采样**（`HeartbeatThread._maybe_sample_disk`）：按
   `STP_DEVICE_DISK_SAMPLE_INTERVAL_SECONDS`（默认 300s，**0/负=关闭**）节流；
   采样值按 serial 缓存，**每拍心跳都带最近一次采样值**（控制面幂等更新）。
   只探测 `adb_state=device` 的设备；与 `_collect_device_infos` 同款
   ThreadPoolExecutor（`_DEVICE_PROBE_MAX_WORKERS=8`）并发，单台 df 假死不拖累整轮。
3. **旋钮**（`HeartbeatSettings.stp_device_disk_sample_interval_seconds`）：刻意
   不进 `_v_positive_pacing` 钳制——「关」是合法档位而非非法值；settings 读取
   失败（#2279 降级 None）＝本拍不采样，心跳照常。
4. **登记**：`environment-variables.md` 表格行 + `backend/agent/.env.example`
   注释态条目（env_inventory 门禁强制「示例登记 ∪ 内部声明」二选一）。

## Alternatives

- **每拍心跳都采**：否决。589 台 fleet 规模下 per-device df 与心跳频率（10-120s）
  耦合，adb 负载不可接受——issue 明写「低频采样即可」。
- **采集塞进 `collect_device_info`**：否决。该函数每拍全量执行，塞进去等于每拍
  采集；采样门控属于 HeartbeatThread 的节奏域，与 #2086/#2279 的 Settings 体系同层。
- **job 执行期顺带采集**：否决。覆盖率绑定 job 调度（闲置设备永远盲），且污染
  job 语义；观测采集属心跳域。
- **`_INTERNAL_ONLY` 声明**：否决。采样间隔是 fleet 规模相关运维旋钮（大 fleet
  调大、调试可关闭），按 env_inventory 验收口径应进示例而非内部声明。

## Verification

- `python -m pytest backend/agent/tests/test_device_disk_observability_2757.py -q`
  → 5 passed：解析三形态 + 垃圾输出保守 None（错误串含 `/data`、纯表头、非数字行）；
  静态设备/adb 异常 → None 字段；门控（首拍采 → 窗口内不重采 → 间隔到重采 →
  0=关 → settings None 不采）+ 只采 device 态；`_tick` 端到端 payload 断言
  （采样值入 payload、offline 设备字段 None 上送）。
- `tests/test_env_inventory.py` → 全绿（新 env 已双登记）。
- `python -m pytest backend/agent/tests/ -q` → **2169 passed**（69.15s）。
- `check_governance_surface.py --check` 全绿；`run_gates.py check:quick` **12 gates
  全绿**。

## Revisit

- **真机验收**：合入 + 部署后按 issue 口径复核覆盖率（`SELECT count(*) FILTER
  (WHERE disk_total IS NOT NULL) FROM device` 应≈在线 MTK+UNISOC 数）；本单验证
  止于单测级，未接真机 df。
- **磁盘告警/指标导出**：本单只打通 DB 字段；`stability_*` 指标族（如
  disk_used/total 比）与「磁盘快满」告警属后续观测面切片（参考 #2643 站点
  可见面分层的教训，控制面域规则不塞站点模板）。
- **前端展示**：DeviceOut 已暴露字段，设备详情页是否加列属 UI 侧另议。
