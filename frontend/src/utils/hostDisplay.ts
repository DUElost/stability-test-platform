import type { Host } from './api/types';

/**
 * 只需要显示名两要素——调用方常持有的是 `{ ip?, name? }` 的查找结果（如
 * `HostLabelLookup`），不是完整 `Host`；结构化最小面让两侧都能直接传。
 */
export interface HostDisplaySource {
  name?: string | null;
  ip?: string | null;
}

/** `unassigned` 不是主机，是「未归属设备」桶——显示名由本文件统一给，避免各页各写一份。 */
export const UNASSIGNED_HOST_KEY = 'unassigned';
export const UNASSIGNED_HOST_LABEL = '未分配节点';

/**
 * 主机显示名的**唯一入口**（#2601）。
 *
 * 优先顺序 `name` > `ip` > `hostId`：与主机页（`HostsPage`）、设备页
 * （`DevicesPage` 的 `host_name`）与报告页（`report.host?.name`）现有口径一致；
 * 选机工作台家族此前是 ip-first（另有 2 处甚至同文件内混用），本函数是它们的收敛点。
 *
 * `hostId` 是内部标识（当前约定为 IP 点分替换成连字符），只在拿不到 name/ip 时兜底
 * ——直接把它当展示值会让运维自己反向翻译主机名（#2601 现场：同一条 host 事实出现
 * 三种读数）。
 *
 * @param host     主机记录；查不到（缓存过旧/已删）时传 null
 * @param hostId   设备行携带的 host_id；只用于兜底与 `unassigned` 判定
 * @param fallback hostId 也拿不到时的文案（默认「未知主机」；表格单元格可传 '—'）
 */
export function hostLabel(
  host: Host | HostDisplaySource | null | undefined,
  hostId?: string | number | null,
  fallback = '未知主机',
): string {
  if (host?.name) return host.name;
  if (host?.ip) return host.ip;
  const id = hostId === null || hostId === undefined ? '' : String(hostId).trim();
  if (!id) return fallback;
  return id === UNASSIGNED_HOST_KEY ? UNASSIGNED_HOST_LABEL : id;
}
