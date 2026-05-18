import axios from 'axios';

// 防抖：避免并发刷新导致重复请求 (axios interceptor + WebSocket auth 共用)
let _refreshInFlight: Promise<string | null> | null = null;

function _base64UrlDecode(input: string): string | null {
  try {
    const normalized = input.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
    if (typeof atob === 'function') {
      return atob(padded);
    }
    return null;
  } catch {
    return null;
  }
}

function _getTokenExpSeconds(token: string): number | null {
  try {
    const parts = token.split('.');
    if (parts.length < 2) return null;
    const payloadRaw = _base64UrlDecode(parts[1]);
    if (!payloadRaw) return null;
    const payload = JSON.parse(payloadRaw) as { exp?: number };
    return typeof payload.exp === 'number' ? payload.exp : null;
  } catch {
    return null;
  }
}

export function upsertWsToken(url: string, token: string): string {
  if (!token) return url;
  try {
    const parsed = new URL(url);
    parsed.searchParams.set('token', token);
    return parsed.toString();
  } catch {
    const hasQuery = url.includes('?');
    const sep = hasQuery ? '&' : '?';
    const hasToken = /[?&]token=/.test(url);
    if (hasToken) {
      return url.replace(/([?&]token=)[^&]*/i, `$1${encodeURIComponent(token)}`);
    }
    return `${url}${sep}token=${encodeURIComponent(token)}`;
  }
}

/**
 * 审计 Frontend #5: 唯一的 refresh token 入口。
 *
 * Why: 旧实现 client.ts interceptor 与 auth.ts 各有一份独立的 refresh 逻辑,
 * 并发 401 会同时调用 /auth/refresh,后端启用 rotation 时拿到 stale refresh_token
 * 会直接拒绝并踢登录。
 * How to apply: client.ts interceptor 与 ensureFreshAccessToken 都走这里,
 * `_refreshInFlight` 保证整页只有一个在飞的 refresh。
 */
export async function refreshAccessToken(): Promise<string | null> {
  if (_refreshInFlight) return _refreshInFlight;

  _refreshInFlight = (async () => {
    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) return null;

    try {
      const response = await axios.post('/api/v1/auth/refresh', {
        refresh_token: refreshToken,
      });
      const { access_token, refresh_token } = response.data || {};
      if (access_token) localStorage.setItem('access_token', access_token);
      if (refresh_token) localStorage.setItem('refresh_token', refresh_token);
      return access_token || null;
    } catch {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
      return null;
    } finally {
      _refreshInFlight = null;
    }
  })();

  return _refreshInFlight;
}

export async function ensureFreshAccessToken(thresholdSeconds = 60): Promise<string | null> {
  const token = localStorage.getItem('access_token');
  if (!token) return null;

  const exp = _getTokenExpSeconds(token);
  if (!exp) return token;

  const now = Math.floor(Date.now() / 1000);
  if (exp - now > thresholdSeconds) return token;

  return refreshAccessToken();
}
