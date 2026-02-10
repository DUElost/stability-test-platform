/**
 * DTO 映射层
 *
 * 将后端 API 返回的数据（蛇形命名）映射到前端组件使用的格式（驼峰命名）
 * 提供类型安全的转换函数
 */

import type { Host, Device } from '@/utils/api';

// ============================================================================
// Host 映射
// ============================================================================

/** 后端返回的 Host DTO */
export interface HostDTO {
  id: number;
  name: string;
  ip: string;
  ssh_port: number;
  ssh_user: string | null;
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED';
  last_heartbeat: string | null;
  extra: Record<string, unknown>;
  mount_status: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

/** 组件使用的 Host 视图模型 */
export interface HostViewModel extends Host {
  /** 挂载状态是否全部正常 */
  mountStatusOk: boolean;
  /** 设备数量 */
  deviceCount: number;
}

/**
 * 将 Host DTO 映射为组件使用的视图模型
 */
export function mapHostToViewModel(
  dto: HostDTO,
  deviceCount: number = 0
): HostViewModel {
  // 计算挂载状态
  const mountStatusOk = Object.values(dto.mount_status || {}).every(
    (v) => (v as { ok?: boolean }).ok === true || v === true
  );

  return {
    id: dto.id,
    name: dto.name,
    ip: dto.ip,
    ssh_port: dto.ssh_port,
    ssh_user: dto.ssh_user,
    status: dto.status,
    last_heartbeat: dto.last_heartbeat,
    extra: dto.extra,
    mount_status: dto.mount_status,
    // 计算属性
    mountStatusOk,
    deviceCount,
  };
}

/**
 * 批量映射 Host DTO 列表
 */
export function mapHostsToViewModels(
  dtos: HostDTO[],
  deviceCountMap?: Map<number, number>
): HostViewModel[] {
  return dtos.map((dto) =>
    mapHostToViewModel(dto, deviceCountMap?.get(dto.id) ?? 0)
  );
}

// ============================================================================
// Device 映射
// ============================================================================

/** 后端返回的 Device DTO */
export interface DeviceDTO {
  id: number;
  serial: string;
  model: string | null;
  host_id: number | null;
  status: 'ONLINE' | 'OFFLINE' | 'BUSY';
  last_seen: string | null;
  tags: string[];
  extra?: Record<string, unknown>;
  // ADB 连接状态
  adb_state?: string | null;
  adb_connected?: boolean | null;
  // 硬件信息
  battery_level?: number | null;
  battery_temp?: number | null;
  temperature?: number | null;
  wifi_rssi?: number | null;
  wifi_ssid?: string | null;
  network_latency?: number | null;
  // 系统资源
  cpu_usage?: number | null;
  mem_total?: number | null;
  mem_used?: number | null;
  disk_total?: number | null;
  disk_used?: number | null;
  // 锁信息
  lock_run_id?: number | null;
  lock_expires_at?: string | null;
}

/** 组件使用的 Device 视图模型 */
export interface DeviceViewModel extends Device {
  /** 主机信息 */
  hostName?: string;
  hostIp?: string;
  /** 是否已连接 */
  isConnected: boolean;
  /** 是否被锁定 */
  isLocked: boolean;
  /** 格式化后的状态显示 */
  displayStatus: 'idle' | 'offline' | 'testing' | 'unknown';
}

/**
 * 将 Device DTO 映射为组件使用的视图模型
 */
export function mapDeviceToViewModel(
  dto: DeviceDTO,
  hostMap?: Map<number, { name: string; ip: string }>
): DeviceViewModel {
  const host = dto.host_id ? hostMap?.get(dto.host_id) : undefined;

  // 状态映射：后端状态 -> 前端显示状态
  const statusMap: Record<string, DeviceViewModel['displayStatus']> = {
    ONLINE: 'idle',
    OFFLINE: 'offline',
    BUSY: 'testing',
  };

  return {
    id: dto.id,
    serial: dto.serial,
    model: dto.model,
    host_id: dto.host_id,
    status: dto.status,
    last_seen: dto.last_seen,
    tags: dto.tags,
    extra: dto.extra,
    // ADB 状态
    adb_state: dto.adb_state,
    adb_connected: dto.adb_connected,
    // 硬件信息
    battery_level: dto.battery_level,
    battery_temp: dto.battery_temp,
    temperature: dto.temperature,
    wifi_rssi: dto.wifi_rssi,
    wifi_ssid: dto.wifi_ssid,
    network_latency: dto.network_latency,
    // 系统资源
    cpu_usage: dto.cpu_usage,
    mem_total: dto.mem_total,
    mem_used: dto.mem_used,
    disk_total: dto.disk_total,
    disk_used: dto.disk_used,
    // 计算属性
    hostName: host?.name,
    hostIp: host?.ip,
    isConnected: dto.adb_connected === true,
    isLocked: dto.lock_run_id != null,
    displayStatus: statusMap[dto.status] ?? 'unknown',
  };
}

/**
 * 批量映射 Device DTO 列表
 */
export function mapDevicesToViewModels(
  dtos: DeviceDTO[],
  hostMap?: Map<number, { name: string; ip: string }>
): DeviceViewModel[] {
  return dtos.map((dto) => mapDeviceToViewModel(dto, hostMap));
}

// ============================================================================
// 类型守卫
// ============================================================================

/**
 * 检查对象是否为 HostDTO
 */
export function isHostDTO(obj: unknown): obj is HostDTO {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    'id' in obj &&
    'name' in obj &&
    'ip' in obj &&
    'status' in obj
  );
}

/**
 * 检查对象是否为 DeviceDTO
 */
export function isDeviceDTO(obj: unknown): obj is DeviceDTO {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    'id' in obj &&
    'serial' in obj &&
    'status' in obj
  );
}

// ============================================================================
// 导出
// ============================================================================

export type { Host, Device } from '@/utils/api';
