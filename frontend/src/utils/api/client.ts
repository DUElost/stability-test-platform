import axios from 'axios';
import { refreshAccessToken } from '@/utils/auth';

export class ApiError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
  }
}

type AuthFailureHandler = () => void;
let _authFailureHandler: AuthFailureHandler | null = null;

export function registerAuthFailureHandler(fn: AuthFailureHandler): void {
  _authFailureHandler = fn;
}

const apiClient = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

function shouldSkipRefresh(url: unknown): boolean {
  const value = typeof url === 'string' ? url : '';
  return value.includes('/auth/login')
    || value.includes('/auth/token')
    || value.includes('/auth/refresh')
    || value.includes('/auth/logout');
}

function isLoginRequest(url: unknown): boolean {
  return typeof url === 'string' && url.includes('/auth/login');
}

apiClient.interceptors.request.use(
  (config) => {
    if (import.meta.env.DEV) console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    return config;
  },
  (error) => {
    if (import.meta.env.DEV) console.error('[API] Request error:', error);
    return Promise.reject(error);
  }
);

apiClient.interceptors.response.use(
  (response) => {
    if (import.meta.env.DEV) console.log(`[API] Response:`, response.data);
    return response;
  },
  async (error) => {
    if (import.meta.env.DEV) console.error('[API] Response error:', error);

    if (error.response?.status === 401) {
      if (error.config && !error.config.__retry && !shouldSkipRefresh(error.config.url)) {
        error.config.__retry = true;
        // 审计 Frontend #5: 走唯一的防抖 refreshAccessToken,避免并发 401 同时多次 refresh。
        // 当前浏览器端已切到 HttpOnly cookie，会话恢复成功后直接重放原请求即可。
        const refreshed = await refreshAccessToken();
        if (refreshed) {
          return apiClient(error.config);
        }
      }

      if (isLoginRequest(error.config?.url)) {
        return Promise.reject(error);
      }

      // 已经在 /login 时跳过 clearAppQueryCache + disconnect + redirect:
      // 否则 useAuthSession 的 /auth/me 探活会在 queryClient.clear() 后立即重发,
      // 再 401 → 再清缓存,造成永久 "校验登录状态中..." 死循环。pathname 已是 /login
      // 时这一整组副作用本就无业务收益。
      if (window.location.pathname === '/login') {
        return Promise.reject(error);
      }

      if (_authFailureHandler) {
        _authFailureHandler();
      } else {
        window.location.href = '/login';
      }
    }

    return Promise.reject(error);
  }
);

export default apiClient;

export async function unwrapApiResponse<T>(
  request: Promise<{ data: { data?: T; error?: { code: string; message: string } | null } }>
): Promise<T> {
  const resp = await request;
  const body = resp.data as { data?: T; error?: { code: string; message: string } | null };
  if (body?.error) throw new ApiError(body.error.code, body.error.message);
  // 审计 Frontend #4: ApiResponse 契约要求 success 必带 data;严格化 null/undefined 兜底
  // Why: 旧版 `return body.data as T` 把 null 当成 T 偷渡,调用方拿到 null 才发现已迟。
  // How to apply: data 缺失视为后端契约违反,直接抛 ApiError(MALFORMED_RESPONSE)。
  if (body == null || !('data' in body) || body.data === undefined) {
    throw new ApiError(
      'MALFORMED_RESPONSE',
      'API response missing both `data` and `error` fields',
    );
  }
  return body.data as T;
}
