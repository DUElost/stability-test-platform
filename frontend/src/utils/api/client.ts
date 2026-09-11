import axios from 'axios';
import { API_TIMEOUT_MS, SESSION_PROBE_TIMEOUT_MS } from './timeouts';
import { refreshAccessToken } from '@/utils/auth';
import type {
  ApiResponseEnvelope,
  StructuredApiError,
} from './types';

export class ApiError extends Error {
  code: string;
  status?: number;
  details?: Record<string, unknown>;
  responseData?: unknown;
  originalError?: unknown;

  constructor(
    code: string,
    message: string,
    options: {
      status?: number;
      details?: Record<string, unknown>;
      responseData?: unknown;
      originalError?: unknown;
    } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = options.status;
    this.details = options.details;
    this.responseData = options.responseData;
    this.originalError = options.originalError;
  }

  get retryable(): boolean | undefined {
    return typeof this.details?.retryable === 'boolean'
      ? this.details.retryable
      : undefined;
  }

  get planRunId(): number | undefined {
    const value = this.details?.plan_run_id;
    return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function validationMessage(value: unknown): string | undefined {
  if (!Array.isArray(value)) return undefined;
  const messages = value
    .map((item) => {
      if (!isRecord(item)) return null;
      const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
      const msg = typeof item.msg === 'string' ? item.msg : '';
      return `${loc} ${msg}`.trim() || null;
    })
    .filter((item): item is string => item !== null);
  return messages.length > 0 ? messages.join('; ') : undefined;
}

function structuredDetails(value: unknown): Record<string, unknown> | undefined {
  return isRecord(value) ? { ...value } : undefined;
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;

  const source = isRecord(error) ? error : undefined;
  // #1199：axios 超时（ECONNABORTED/ETIMEDOUT 或 message 含 timeout）映射为
  // 可辨识文案与 TIMEOUT 码，避免暴露 "timeout of 30000ms exceeded" 这类技术串
  const isTimeout =
    source?.code === 'ECONNABORTED' ||
    source?.code === 'ETIMEDOUT' ||
    (typeof source?.message === 'string' && /timeout/i.test(source.message));
  const response = isRecord(source?.response) ? source.response : undefined;
  const status = typeof response?.status === 'number' ? response.status : undefined;
  const responseData = response?.data;
  const responseBody = isRecord(responseData) ? responseData : undefined;
  const candidate =
    responseBody?.error ??
    responseBody?.detail ??
    responseData;
  const details = structuredDetails(candidate);

  const message =
    (details && typeof details.message === 'string' ? details.message : undefined) ??
    (typeof candidate === 'string' ? candidate : undefined) ??
    validationMessage(candidate) ??
    (isTimeout ? '请求超时，请重试' : undefined) ??
    (typeof source?.message === 'string' ? source.message : undefined) ??
    (status ? `请求失败 (${status})` : '网络请求失败');
  const code =
    (details && typeof details.code === 'string' ? details.code : undefined) ??
    (status ? `HTTP_${status}` : isTimeout ? 'TIMEOUT' : 'NETWORK_ERROR');

  return new ApiError(code, message, {
    status,
    details,
    responseData,
    originalError: error,
  });
}

type AuthFailureHandler = () => void;
let _authFailureHandler: AuthFailureHandler | null = null;

export function registerAuthFailureHandler(fn: AuthFailureHandler): void {
  _authFailureHandler = fn;
}

const apiClient = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
  // #1199：普通 REST 的应用层超时上界（长请求在各自调用点豁免为 NO_TIMEOUT）
  timeout: API_TIMEOUT_MS,
  headers: {
    'Content-Type': 'application/json',
  },
});

function shouldSkipRefresh(url: unknown): boolean {
  const value = typeof url === 'string' ? url : '';
  return value.includes('/auth/login')
    || value.includes('/auth/register')
    || value.includes('/auth/token')
    || value.includes('/auth/refresh')
    || value.includes('/auth/logout');
}

/**
 * 公开认证页（#1191）：这些页面上的 /auth/me 探活 401 是「未登录访客」的
 * 正常形态，不是会话终态——不做 clearAppQueryCache/disconnect/redirect 副作用。
 */
const AUTH_PUBLIC_PATHS = ['/login', '/register'];

function isAuthPublicPath(pathname: string): boolean {
  return AUTH_PUBLIC_PATHS.includes(pathname.replace(/\/+$/, '') || '/');
}

function isLoginRequest(url: unknown): boolean {
  return typeof url === 'string' && url.includes('/auth/login');
}

// 裸 axios 调用，不经过本拦截器（防递归）。
async function isSessionAlive(): Promise<boolean> {
  try {
    await axios.get('/api/v1/auth/me', {
      withCredentials: true,
      // #1199：探活位于 401 恢复路径内，挂起会拖住拦截器，收紧超时
      timeout: SESSION_PROBE_TIMEOUT_MS,
    });
    return true;
  } catch {
    return false;
  }
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
        return Promise.reject(toApiError(error));
      }

      // 公开认证页（/login、/register）跳过 clearAppQueryCache + disconnect + redirect：
      // 未登录冷启动这些页面时 /auth/me 探活 401 是预期形态（#1191）；/login 上
      // 还会因 queryClient.clear() 后立即重发探活形成「校验登录状态中...」死循环。
      if (isAuthPublicPath(window.location.pathname)) {
        return Promise.reject(toApiError(error));
      }

      // #1039：refresh 失败 ≠ 会话已死——多标签 rotation 竞态下本标签的旧
      // jti 可能刚被另一标签的消费输掉，而赢家的新 cookie 已在 jar 里。探活
      // 成功则直接重放原请求而不是全局登出；探活也失败才认定会话终态。
      // __probeRetry 防重放后的 401 再次进入本分支造成循环。
      if (
        error.config
        && !error.config.__probeRetry
        && !shouldSkipRefresh(error.config.url)
      ) {
        error.config.__probeRetry = true;
        if (await isSessionAlive()) {
          return apiClient(error.config);
        }
      }

      if (_authFailureHandler) {
        _authFailureHandler();
      } else {
        window.location.href = '/login';
      }
    }

    return Promise.reject(toApiError(error));
  }
);

export default apiClient;

export async function unwrapApiResponse<T>(
  request: Promise<{ data: ApiResponseEnvelope<T> }>
): Promise<T> {
  const resp = await request;
  const body = resp.data;
  if (body?.error) {
    const detail = body.error as StructuredApiError;
    throw new ApiError(detail.code, detail.message, {
      details: { ...detail },
      responseData: body,
    });
  }
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
