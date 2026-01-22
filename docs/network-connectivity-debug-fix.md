# 网络连接显示 Offline 问题修复记录

> **问题**：设备已连接网络且手动 ping 成功，但前端界面仍显示 offline
>
> **解决时间**：2026-01-22
>
> **影响范围**：`device_discovery.py`, `main.py`, `heartbeat.py`, `Dashboard.tsx`, `api.ts`

---

## 问题现象

### 初始症状
- 手机设备已连接 WiFi，手动 ping 8.8.8.8 和 223.5.5.5 均成功
- 前端界面温度从 27°C 更新到 32°C，说明数据流正常
- 但 Network 仍显示 `offline`，而非 `online`

### 用户测试数据
```bash
# 手动 ping 测试成功
adb shell ping -c 3 223.5.5.5
# rtt min/avg/max/mdev = 25.307/33.796/49.445/11.078 ms

adb shell ping -c 3 8.8.8.8
# rtt min/avg/max/mdev = 227.749/335.311/442.874/107.564 ms
```

---

## 诊断过程

### 阶段 1：前端诊断（Gemini）

**发现的问题**：
1. **缺少自动刷新机制**：`QueryProvider` 禁用了 `refetchOnWindowFocus`，且未设置轮询
2. **类型定义不完整**：`api.ts` 中 `Device` 接口缺少 `extra` 字段
3. **潜在的数据处理问题**：使用 `||` 可能导致 0 被误判为 null

**诊断结论**：前端展示的是页面加载时的快照，即使后端数据更新也不会自动刷新。

### 阶段 2：后端诊断（Codex）

**发现的问题**：
1. **`_parse_ping_time` 解析顺序错误**：
   - 代码在单次循环中先检查 `time=` 行，找到就立即返回
   - 导致返回第一个 `time=` 值（如 26.6ms）而不是 `rtt` 汇总行的平均值（如 33.796ms）
2. **调试日志不足**：使用 `logger.debug` 级别，无法在生产环境中追踪问题
3. **数据流追踪缺失**：无法确认 `network_latency` 是否正确从 Agent 传递到前端

### 阶段 3：数据流验证

**后端日志确认**：
```
ping_parse_success: MTK0002503011717, target=8.8.8.8, latency=451.89ms
```

**问题定位**：ping 解析成功，但数据未正确传递到前端。

---

## 根本原因

| 层级 | 问题 | 影响 |
|------|------|------|
| **前端** | 缺少自动刷新机制 | UI 不更新 |
| **前端** | 类型定义不完整 | TypeScript 类型不匹配 |
| **前端** | 使用 `||` 可能误判 0 值 | 数据处理错误 |
| **后端** | `_parse_ping_time` 解析顺序错误 | 返回错误值 |
| **后端** | 调试日志级别过低 | 无法追踪问题 |
| **后端** | 数据流日志缺失 | 无法确认传递状态 |

---

## 修复方案

### 1. 前端自动刷新

**文件**：`frontend/src/pages/Dashboard.tsx`

```diff
  const { data: hosts, isLoading: hostsLoading, error: hostsError } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => api.hosts.list().then(res => res.data),
+   refetchInterval: 5000, // 每 5 秒轮询一次
  });

  const { data: devices, isLoading: devicesLoading, error: devicesError } = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.devices.list().then(res => res.data),
+   refetchInterval: 5000, // 每 5 秒轮询一次
  });
```

### 2. 前端数据处理修复

**文件**：`frontend/src/pages/Dashboard.tsx`

```diff
  battery_level: device.extra?.battery_level || 0,
+ battery_level: device.extra?.battery_level ?? 0,
  temperature: device.extra?.temperature || 0,
+ temperature: device.extra?.temperature ?? 0,
  network_latency: device.extra?.network_latency || null,
+ network_latency: device.extra?.network_latency ?? null,
```

### 3. 前端类型定义补全

**文件**：`frontend/src/utils/api.ts`

```diff
  export interface Device {
    id: number;
    serial: string;
    model: string | null;
    host_id: number | null;
    status: 'ONLINE' | 'OFFLINE' | 'BUSY';
    last_seen: string | null;
    tags: string[];
+   extra?: Record<string, any>; // 设备扩展数据：battery_level, temperature, network_latency
  }
```

### 4. 后端解析逻辑重构

**文件**：`backend/agent/device_discovery.py`

**修改前**：
```python
def _parse_ping_time(text: str) -> float | None:
    for line in text.splitlines():
        if "rtt min/avg/max/mdev" in line:
            # 解析平均值...
        if "time=" in line:  # 先检查这个，立即返回！
            return float(time)  # 返回单个值而非平均值
```

**修改后**：
```python
def _parse_ping_time(text: str) -> float | None:
    lines = text.splitlines()

    # 第一遍：优先查找 rtt 汇总行（包含平均值）
    for line in lines:
        if "rtt min/avg/max/mdev" in line or "round-trip" in line:
            parts = line.split("=")[1].strip().split("/")
            if len(parts) >= 2:
                avg_str = parts[1].strip().replace("ms", "").strip()
                return float(avg_str)  # 返回平均值

    # 第二遍：查找 time= 行（返回最后一个值）
    last_time = None
    for line in lines:
        if "time=" in line and "bytes from" in line:
            for part in line.split():
                if part.startswith("time="):
                    time_str = part.split("=")[1].replace("ms", "").strip()
                    try:
                        last_time = float(time_str)
                    except:
                        pass

    return last_time
```

### 5. 后端调试日志增强

**文件**：`backend/agent/device_discovery.py`

```python
def _ping_with_fallback(adb_path: str, serial: str, target: str, fallback: str | None = None):
    def _ping(host: str) -> tuple[float | None, bool]:
        result = subprocess.run([...])

        # 记录原始输出用于调试
        logger.info(f"ping_raw_output: {serial}, target={host}, returncode={result.returncode}")
        logger.info(f"ping_stdout: {serial}, stdout={result.stdout[:500] if result.stdout else 'empty'}")

        if result.returncode != 0:
            logger.warning(f"ping_returncode_failed: {serial}, target={host}, returncode={result.returncode}")
            return None, False

        latency = _parse_ping_time(result.stdout)
        if latency is not None:
            logger.info(f"ping_parse_success: {serial}, target={host}, latency={latency}ms")
        else:
            logger.warning(f"ping_parse_failed: {serial}, target={host}, could not parse latency from output")

        return latency, latency is not None
```

### 6. 数据流追踪日志

**文件**：`backend/agent/main.py`

```python
for dev in discovered:
    info = device_discovery.collect_device_info(adb_path, dev["serial"])
    device_data = {
        "serial": dev["serial"],
        "model": dev.get("model"),
        "state": dev["adb_state"],
        "battery_level": info.get("battery_level"),
        "temperature": info.get("temperature"),
        "network_latency": info.get("network_latency"),
    }
    devices_list.append(device_data)
    # 记录设备数据用于调试
    logger.info(f"device_collected: {dev['serial']}, network_latency={info.get('network_latency')}, battery={info.get('battery_level')}, temp={info.get('temperature')}")
```

**文件**：`backend/agent/heartbeat.py`

```python
# 记录设备数据用于调试
if devices:
    for dev in devices:
        logger.info(f"heartbeat_device: serial={dev.get('serial')}, network_latency={dev.get('network_latency')}, battery={dev.get('battery_level')}, temp={dev.get('temperature')}")

resp = requests.post(f"{api_url}/api/v1/heartbeat", json=payload, timeout=5)
logger.info(f"heartbeat_success: host_id={host_id}, devices_count={len(devices or [])}, response={resp.json()}")
```

### 7. DNS 顺序调换

**文件**：`backend/agent/device_discovery.py`

```diff
- # 采集网络延迟 (主目标 8.8.8.8, 备用 223.5.5.5)
- latency = _ping_with_fallback(adb_path, serial, "8.8.8.8", fallback="223.5.5.5")
+ # 采集网络延迟 (主目标 223.5.5.5, 备用 8.8.8.8)
+ latency = _ping_with_fallback(adb_path, serial, "223.5.5.5", fallback="8.8.8.8")
```

**原因**：223.5.5.5（阿里 DNS）在国内访问更快更稳定。

---

## 修复验证

### 预期日志输出

```bash
# Agent 采集日志
device_collected: MTK0002503011717, network_latency=24.874, battery=85, temp=32

# 心跳发送日志
heartbeat_device: serial=MTK0002503011717, network_latency=24.874, battery=85, temp=32
heartbeat_success: host_id=1, devices_count=1, response={'ok': True, 'devices_count': 1}

# Ping 解析日志
ping_raw_output: MTK0002503011717, target=223.5.5.5, returncode=0
ping_parse_success: MTK0002503011717, target=223.5.5.5, latency=24.874ms
```

### 前端显示

**修复前**：
```
Network: offline (无延迟显示)
```

**修复后**：
```
Network: online (24.874ms)  ← 绿色，显示延迟
```

---

## 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│ Windows 主机 (172.21.10.x)                                  │
│ ├── FastAPI 后端 :8000                                      │
│ └── React 前端    :5173  ← 每 5 秒轮询 /api/v1/devices      │
└─────────────────────────────────────────────────────────────┘
                          ▲ HTTP 心跳 (每 5 秒)
                          │
┌─────────────────────────────────────────────────────────────┐
│ Linux Agent 主机 (172.21.15.*)                              │
│ ├── Python Agent                                            │
│ ├── ping 223.5.5.5 (主) → 解析延迟                         │
│ └── ADB → Android 设备                                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 代码部署

### Windows 后端/前端

```bash
# 代码已修改，无需特殊操作
# 如使用 --reload 模式会自动重载
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### Linux Agent

```bash
# 同步代码
scp backend/agent/device_discovery.py android@172.21.15.1:/opt/stability-test-agent/backend/agent/
scp backend/agent/main.py android@172.21.15.1:/opt/stability-test-agent/backend/agent/
scp backend/agent/heartbeat.py android@172.21.15.1:/opt/stability-test-agent/backend/agent/

# 重启服务
sudo systemctl restart stability-agent

# 查看日志
sudo journalctl -u stability-agent -f --since "1 minute ago"
```

---

## 经验总结

### 调试技巧

1. **分层诊断**：前端 → 后端 → 数据流，逐层验证
2. **日志优先**：生产环境使用 `logger.info` 而非 `logger.debug`
3. **数据追踪**：在关键节点添加日志确认数据传递
4. **增量修复**：每次修复后验证，避免引入新问题

### 常见陷阱

1. **短路逻辑**：`||` 会将 0、false、"" 视为 falsy，应使用 `??`
2. **解析顺序**：在循环中先检查条件会提前返回，导致遗漏更优解
3. **自动刷新**：监控类仪表盘必须配置轮询或实时推送
4. **类型定义**：前端类型定义不完整会导致 TypeScript 编译错误或运行时问题

### 性能考虑

- **轮询间隔**：5 秒轮询在设备数量较少（<100）时性能影响可接受
- **后续优化**：可考虑使用 WebSocket 实现实时推送，减少轮询开销

---

## 相关文件清单

| 文件 | 修改内容 |
|------|----------|
| `frontend/src/pages/Dashboard.tsx` | 添加轮询、修复数据处理 |
| `frontend/src/utils/api.ts` | 补全 Device 接口定义 |
| `backend/agent/device_discovery.py` | 重构解析逻辑、增强日志、调换 DNS |
| `backend/agent/main.py` | 添加数据流追踪日志 |
| `backend/agent/heartbeat.py` | 添加心跳发送日志 |

---

*最后更新时间：2026-01-22*
