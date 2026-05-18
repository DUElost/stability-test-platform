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

const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
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
      const refreshToken = localStorage.getItem('refresh_token');
      if (refreshToken && error.config && !error.config.__retry) {
        error.config.__retry = true;
        // 审计 Frontend #5: 走唯一的防抖 refreshAccessToken,避免并发 401 同时多次 refresh
        // 导致 refresh_token rotation 失效被踢登录。
        const newAccess = await refreshAccessToken();
        if (newAccess) {
          error.config.headers.Authorization = `Bearer ${newAccess}`;
          return apiClient(error.config);
        }
        // refreshAccessToken 在失败时已清理本地 token 并跳转 /login
        return Promise.reject(error);
      } else {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
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
