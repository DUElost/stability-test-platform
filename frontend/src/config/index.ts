/**
 * 前端配置文件
 *
 * 环境变量优先级高于默认值。
 * 生产构建请设 VITE_API_BASE_URL= （空）使 API / SocketIO 走 Nginx 同源。
 */

const isLocalhost =
  window.location.hostname === 'localhost'
  || window.location.hostname === '127.0.0.1';

const envApiUrl = import.meta.env.VITE_API_BASE_URL as string | undefined;

// 空字符串或未设置 → 非 localhost 时用当前页面 origin（Nginx 反代 /api + /socket.io）
export const API_BASE_URL =
  envApiUrl !== undefined && envApiUrl !== ''
    ? envApiUrl
    : isLocalhost
      ? 'http://localhost:8000'
      : window.location.origin;

const envWsUrl = import.meta.env.VITE_WS_BASE_URL as string | undefined;

export const WS_BASE_URL =
  envWsUrl !== undefined && envWsUrl !== ''
    ? envWsUrl
    : isLocalhost
      ? 'ws://localhost:8000'
      : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;

// WebSocket 端点 (legacy format — useSocketIO maps these to SocketIO rooms)
export const WS_DASHBOARD_ENDPOINT = `${WS_BASE_URL}/ws/dashboard`;

/** SocketIO dashboard namespace URL（空 API_BASE_URL 时走相对路径 /dashboard） */
export function dashboardSocketUrl(): string {
  if (!envApiUrl && !isLocalhost) {
    return '/dashboard';
  }
  return `${API_BASE_URL}/dashboard`;
}
