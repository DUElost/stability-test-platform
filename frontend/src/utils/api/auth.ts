import apiClient from './client';
import type { User } from './types';

type SessionResponse = { ok: boolean };

export const auth = {
  me: () => apiClient.get<User>('/auth/me'),
  login: (username: string, password: string) =>
    apiClient.post<SessionResponse>(
      '/auth/login',
      new URLSearchParams({ username, password }),
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
    ),
  register: (data: { username: string; password: string; role?: string }) =>
    apiClient.post<User>('/auth/register', data),
  refresh: () =>
    apiClient.post<SessionResponse>(
      '/auth/refresh',
    ),
  logout: () => apiClient.post<{ ok: boolean }>('/auth/logout'),
};
