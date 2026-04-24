import apiClient from './client';
import type { User } from './types';

export const auth = {
  me: () => apiClient.get<User>('/auth/me'),
  login: (username: string, password: string) =>
    apiClient.post<{ access_token: string; refresh_token: string; token_type: string }>(
      '/auth/login',
      new URLSearchParams({ username, password }),
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
    ),
  register: (data: { username: string; password: string; role?: string }) =>
    apiClient.post<User>('/auth/register', data),
  refresh: (refreshToken: string) =>
    apiClient.post<{ access_token: string; refresh_token: string; token_type: string }>(
      '/auth/refresh',
      { refresh_token: refreshToken }
    ),
};
