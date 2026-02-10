/**
 * 前端配置文件
 *
 * 环境变量优先级高于默认值
 */

// API 基础 URL
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ||
  (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : `http://${window.location.hostname}:8000`);

// WebSocket URL
export const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL ||
  (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'ws://localhost:8000'
    : `ws://${window.location.hostname}:8000`);

// WebSocket 端点
export const WS_DASHBOARD_ENDPOINT = `${WS_BASE_URL}/ws/dashboard`;
