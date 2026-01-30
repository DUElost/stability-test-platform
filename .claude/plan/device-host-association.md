# 功能实施规划：设备与主机关联信息显示

**规划时间**：2026-01-30
**预估工作量**：8 任务点

---

## 1. 功能概述

### 1.1 目标
在稳定性测试平台前端实现设备与主机之间的关联信息展示：
- **Device Management 页面**：每个设备卡片显示其所属的主机信息
- **Host Management 页面**：每个主机卡片显示连接的设备数量

### 1.2 范围
**包含**：
- DeviceCard 组件添加 Host 标签显示
- HostCard 组件添加设备数量 Badge
- DevicesPage 同时加载 hosts 数据并构建映射关系
- HostsPage 同时加载 devices 数据并计算设备数量

**不包含**：
- 后端 API 修改（假设已有 host_id 关联）
- 新增数据库字段
- 复杂的数据联动实时更新（WebSocket）

### 1.3 技术约束
- React 18 + TypeScript
- Tailwind CSS
- React Query (TanStack Query) 数据获取
- Lucide React 图标库

---

## 2. WBS 任务分解

### 2.1 任务清单

#### 模块 A：DeviceCard 组件修改（2 任务点）

**文件**: `frontend/src/components/device/DeviceCard.tsx`

- [ ] **任务 A.1**：扩展 Device 接口添加 host 相关字段（0.5 点）
  - 在 Device 接口中添加 `host_name?: string` 和 `host_id?: number | null`
  - 确保向后兼容（可选字段）

- [ ] **任务 A.2**：添加 Host 标签 UI（1.5 点）
  - 导入 Server 图标：`import { Server } from 'lucide-react'`
  - 在标题区域下方添加 Host 标签
  - 实现条件样式：有 host_name 时用 `bg-slate-100 text-slate-600`，无 host_name 时用 `bg-amber-50 text-amber-600`

#### 模块 B：HostCard 组件修改（2 任务点）

**文件**: `frontend/src/components/network/HostCard.tsx`

- [ ] **任务 B.1**：扩展 Host 接口添加 device_count 字段（0.5 点）
  - 在 Host 接口中添加 `device_count?: number`

- [ ] **任务 B.2**：添加设备数量 Badge UI（1.5 点）
  - 导入 Smartphone 图标：`import { Smartphone } from 'lucide-react'`
  - 在标题行右侧添加设备数量 Badge
  - 样式：`bg-blue-50 text-blue-700 rounded-full px-2 py-0.5`

#### 模块 C：DevicesPage 页面修改（2 任务点）

**文件**: `frontend/src/pages/devices/DevicesPage.tsx`

- [ ] **任务 C.1**：添加 hosts 查询（0.5 点）
  - 添加 hosts 的 useQuery hook
  - 使用相同的 refetchInterval (10000ms) 保持同步

- [ ] **任务 C.2**：构建 hostMap 并转换 device 数据（1.5 点）
  - 使用 useMemo 构建 hostMap (id -> host)
  - 修改 toComponentDevice 函数，接收 hostMap 参数
  - 根据 device.host_id 查找对应的 host_name

#### 模块 D：HostsPage 页面修改（2 任务点）

**文件**: `frontend/src/pages/hosts/HostsPage.tsx`

- [ ] **任务 D.1**：添加 devices 查询（0.5 点）
  - 添加 devices 的 useQuery hook
  - 使用相同的 refetchInterval (10000ms) 保持同步

- [ ] **任务 D.2**：计算 deviceCountMap 并转换 host 数据（1.5 点）
  - 使用 useMemo 计算 deviceCountMap (host_id -> count)
  - 修改 toComponentHost 函数，接收 deviceCountMap 参数
  - 根据 host.id 查找对应的 device_count

---

## 3. 依赖关系

```
任务 A.1 (Device接口扩展) --> 任务 A.2 (Host标签UI)
任务 B.1 (Host接口扩展) --> 任务 B.2 (设备数量Badge)
任务 A.2 --> 任务 C.2 (DevicesPage数据转换)
任务 B.2 --> 任务 D.2 (HostsPage数据转换)
任务 C.1 (DevicesPage添加hosts查询) --> 任务 C.2
任务 D.1 (HostsPage添加devices查询) --> 任务 D.2
```

**可并行任务**：
- 任务 A.1, A.2 ∥ 任务 B.1, B.2（组件修改相互独立）
- 任务 C.1 ∥ 任务 D.1（页面数据获取相互独立）

---

## 4. UI/UX 设计规范

### 4.1 DeviceCard 布局

```
+------------------------------------------+
|  DeviceCard                              |
|  +------------------------------------+  |
|  | [Model]          [Status: idle]    |  |
|  | serial: abc123                     |  |
|  +------------------------------------+  |
|  | [Host: 172.21.15.10]  <-- 新增      |  |
|  +------------------------------------+  |
|  | Battery: [====] 85%  Temp: 32°C    |  |
|  | Network: [online] 45ms             |  |
|  +------------------------------------+  |
+------------------------------------------+
```

### 4.2 HostCard 布局

```
+------------------------------------------+
|  HostCard                                |
|  +------------------------------------+  |
|  | 172.21.15.10  [5] [online]         |  |
|  | Host Node          ^ 设备数量       |  |
|  +------------------------------------+  |
|  | CPU: [======] 45%                  |  |
|  | RAM: [======] 60%                  |  |
|  | Disk: [======] 70%                 |  |
|  +------------------------------------+  |
|  | Storage Mount: [MOUNTED]           |  |
|  +------------------------------------+  |
+------------------------------------------+
```

### 4.3 样式规范

| 元素 | 颜色 | Tailwind 类 |
|------|------|-------------|
| Host 标签背景（正常） | 浅灰 | `bg-slate-100` |
| Host 标签文字（正常） | 深灰 | `text-slate-600` |
| Host 标签（信息不完整） | 琥珀色 | `bg-amber-50 text-amber-600` |
| 设备数量 Badge 背景 | 浅蓝 | `bg-blue-50` |
| 设备数量 Badge 文字 | 深蓝 | `text-blue-700` |

---

## 5. 代码修改示例

### 5.1 DeviceCard.tsx

```typescript
// 扩展 Device 接口
export interface Device {
  serial: string;
  model: string;
  status: 'idle' | 'testing' | 'offline' | 'error';
  battery_level: number;
  temperature: number;
  network_latency?: number | null;
  current_task?: string;
  host_name?: string;      // 新增
  host_id?: number | null; // 新增
}

// 添加 Host 标签 UI
<div className="flex justify-between items-start mb-2">
  <div>
    <h4 className="font-bold text-slate-800 text-sm">{device.model}</h4>
    <p className="text-xs font-mono text-slate-500">{device.serial}</p>
    {/* 新增 Host 标签 */}
    <div className="flex items-center gap-1 mt-1">
      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${
        device.host_name
          ? 'bg-slate-100 text-slate-600'
          : 'bg-amber-50 text-amber-600'
      }`}>
        <Server size={10} />
        {device.host_name || (device.host_id ? `Host-${device.host_id}` : '未分配')}
      </span>
    </div>
  </div>
  {/* ... 状态标签 ... */}
</div>
```

### 5.2 HostCard.tsx

```typescript
// 扩展 Host 接口
export interface Host {
  ip: string;
  status: 'online' | 'offline' | 'warning';
  cpu_load: number;
  ram_usage: number;
  disk_usage: number;
  mount_status: boolean;
  device_count?: number; // 新增
}

// 修改标题行区域
<div className="flex justify-between items-start mb-4">
  <div>
    <h3 className="font-semibold text-slate-900">{host.ip}</h3>
    <p className="text-xs text-slate-500 mt-0.5">Host Node</p>
  </div>
  <div className="flex items-center gap-2">
    {/* 新增设备数量 Badge */}
    {host.device_count !== undefined && (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 text-xs font-medium">
        <Smartphone size={12} />
        {host.device_count}
      </span>
    )}
    <ConnectivityBadge status={host.status} />
  </div>
</div>
```

### 5.3 DevicesPage.tsx

```typescript
// 添加 hosts 查询
const { data: hosts } = useQuery({
  queryKey: ['hosts'],
  queryFn: () => api.hosts.list().then(res => res.data),
  refetchInterval: 10000,
});

// 构建 hostMap
const hostMap = useMemo(() => {
  if (!hosts) return new Map();
  return new Map(hosts.map((h: any) => [h.id, h]));
}, [hosts]);

// 修改 toComponentDevice 函数
function toComponentDevice(device: any, hostMap: Map<number, any>): Device {
  const host = device.host_id ? hostMap.get(device.host_id) : null;
  return {
    serial: device.serial,
    model: device.model || 'Unknown',
    status: deviceStatusMap[device.status] || 'offline',
    battery_level: device.battery_level ?? 0,
    temperature: device.temperature ?? 0,
    network_latency: device.network_latency ?? null,
    host_id: device.host_id,
    host_name: host?.name || host?.ip || null,
  };
}
```

### 5.4 HostsPage.tsx

```typescript
// 添加 devices 查询
const { data: devices } = useQuery({
  queryKey: ['devices'],
  queryFn: () => api.devices.list().then(res => res.data),
  refetchInterval: 10000,
});

// 计算 deviceCountMap
const deviceCountMap = useMemo(() => {
  if (!devices) return new Map();
  const countMap = new Map<number, number>();
  devices.forEach((device: any) => {
    if (device.host_id) {
      const current = countMap.get(device.host_id) || 0;
      countMap.set(device.host_id, current + 1);
    }
  });
  return countMap;
}, [devices]);

// 修改 toComponentHost 函数
function toComponentHost(host: any, deviceCountMap: Map<number, number>): Host {
  return {
    ip: host.ip,
    status: hostStatusMap[host.status] || 'offline',
    cpu_load: host.extra?.cpu_load || 0,
    ram_usage: host.extra?.ram_usage || 0,
    disk_usage: host.extra?.disk_usage?.usage_percent || 0,
    mount_status: Object.values(host.mount_status || {}).every((v: any) => v.ok || v === true),
    device_count: deviceCountMap.get(host.id) || 0,
  };
}
```

---

## 6. 验收标准

- [ ] DeviceCard 组件显示 Host 标签（有 host_name 时显示名称，无名称时显示 host_id，无 host_id 时显示"未分配"）
- [ ] HostCard 组件显示设备数量 Badge（显示连接的设备数量）
- [ ] DevicesPage 同时加载 devices 和 hosts 数据
- [ ] HostsPage 同时加载 hosts 和 devices 数据
- [ ] 样式符合设计要求（颜色、图标、布局）
- [ ] 数据刷新时关联信息同步更新
- [ ] TypeScript 编译无错误

---

## 7. 验证步骤

### 7.1 开发环境验证

1. **启动后端服务**
   ```bash
   cd stability-test-platform
   uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
   ```

2. **启动前端服务**
   ```bash
   cd stability-test-platform/frontend
   npm run dev
   ```

### 7.2 功能验证

| 验证项 | 操作步骤 | 预期结果 |
|--------|----------|----------|
| DeviceCard Host 标签 | 访问 `/devices` 页面 | 每个设备卡片显示所属 Host 信息 |
| HostCard 设备数量 | 访问 `/hosts` 页面 | 每个主机卡片显示连接设备数量 |
| 未分配设备 | 将某 device 的 host_id 设为 null | 该设备显示"未分配"（琥珀色标签） |
| 数量更新 | 添加/删除设备后刷新页面 | 主机卡片上的设备数量正确更新 |

---

## 8. 后续优化方向（可选）

- **实时更新**：使用 WebSocket 推送设备数量变化
- **点击跳转**：点击 Host 标签可跳转到对应 Host 详情
- **筛选功能**：按 Host 筛选设备列表
- **排序功能**：按设备数量排序主机列表
