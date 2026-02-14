// WebSocket URL configuration
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// Convert http to ws, https to wss
export const WS_URL = API_BASE.replace(/^http/, 'ws');

// Other config values
export const API_URL = API_BASE;
export const DEFAULT_PAGE_SIZE = 10;
export const REFRESH_INTERVAL = 10000; // 10 seconds
