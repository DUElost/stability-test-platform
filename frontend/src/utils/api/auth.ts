import apiClient from './client';
import type { User } from './types';

type SessionResponse = { ok: boolean };

export const auth = {
  me: () => apiClient.get<User>('/auth/me').then(r => r.data),
  login: (username: string, password: string) =>
    apiClient.post<SessionResponse>(
      '/auth/login',
      new URLSearchParams({ username, password }),
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
    ).then(r => r.data),
  register: (data: { username: string; password: string; role?: string }) =>
    apiClient.post<User>('/auth/register', data).then(r => r.data),
  refresh: () =>
    apiClient.post<SessionResponse>('/auth/refresh').then(r => r.data),
  logout: () => apiClient.post<{ ok: boolean }>('/auth/logout').then(r => r.data),
};
