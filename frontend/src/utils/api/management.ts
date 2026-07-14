import apiClient from './client';
import type {
  User, NotificationChannel, AlertRule, TaskSchedule, TaskScheduleCreatePayload,
  TaskScheduleUpdatePayload, ScheduleRunNowResult, PaginatedResponse,
  NotificationLogsResponse, UnreadCountResponse,
} from './types';

export const users = {
  list: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<User>>('/users', { params: { skip, limit } }).then(r => r.data),
  get: (id: number) => apiClient.get<User>(`/users/${id}`).then(r => r.data),
  create: (data: { username: string; password: string; role: string }) =>
    apiClient.post<User>('/users', data).then(r => r.data),
  update: (id: number, data: { username?: string; password?: string; role?: string; is_active?: string }) =>
    apiClient.put<User>(`/users/${id}`, data).then(r => r.data),
  delete: (id: number) => apiClient.delete<void>(`/users/${id}`).then(r => r.data),
  toggleActive: (id: number) => apiClient.post<User>(`/users/${id}/toggle-active`).then(r => r.data),
  changePassword: (data: { old_password: string; new_password: string }) =>
    apiClient.post('/users/change-password', data).then(r => r.data),
};

export const notifications = {
  listChannels: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<NotificationChannel>>('/notifications/channels', { params: { skip, limit } }).then(r => r.data),
  createChannel: (data: { name: string; type: string; config: Record<string, any>; enabled?: boolean }) =>
    apiClient.post<NotificationChannel>('/notifications/channels', data).then(r => r.data),
  updateChannel: (id: number, data: Partial<{ name: string; type: string; config: Record<string, any>; enabled: boolean }>) =>
    apiClient.put<NotificationChannel>(`/notifications/channels/${id}`, data).then(r => r.data),
  deleteChannel: (id: number) => apiClient.delete<void>(`/notifications/channels/${id}`).then(r => r.data),
  testChannel: (id: number) =>
    apiClient.post<{ ok: boolean; message: string }>(`/notifications/channels/${id}/test`).then(r => r.data),
  listRules: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<AlertRule>>('/notifications/rules', { params: { skip, limit } }).then(r => r.data),
  createRule: (data: { name: string; event_type: string; channel_id: number; filters?: Record<string, any>; enabled?: boolean }) =>
    apiClient.post<AlertRule>('/notifications/rules', data).then(r => r.data),
  updateRule: (id: number, data: Partial<{ name: string; event_type: string; channel_id: number; filters: Record<string, any>; enabled: boolean }>) =>
    apiClient.put<AlertRule>(`/notifications/rules/${id}`, data).then(r => r.data),
  deleteRule: (id: number) => apiClient.delete<void>(`/notifications/rules/${id}`).then(r => r.data),
  listLogs: (skip = 0, limit = 50, unreadOnly = false) =>
    apiClient.get<NotificationLogsResponse>('/notifications/logs', { params: { skip, limit, unread_only: unreadOnly } }).then(r => r.data),
  unreadCount: () =>
    apiClient.get<UnreadCountResponse>('/notifications/logs/unread-count').then(r => r.data),
  markRead: (id: number) =>
    apiClient.patch<{ ok: boolean }>(`/notifications/logs/${id}/read`).then(r => r.data),
  markAllRead: () =>
    apiClient.post<{ ok: boolean }>('/notifications/logs/read-all').then(r => r.data),
};

export const schedules = {
  list: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<TaskSchedule>>('/schedules', { params: { skip, limit } }).then(r => r.data),
  get: (id: number) => apiClient.get<TaskSchedule>(`/schedules/${id}`).then(r => r.data),
  create: (data: TaskScheduleCreatePayload) => apiClient.post<TaskSchedule>('/schedules', data).then(r => r.data),
  update: (id: number, data: TaskScheduleUpdatePayload) => apiClient.put<TaskSchedule>(`/schedules/${id}`, data).then(r => r.data),
  delete: (id: number) => apiClient.delete<void>(`/schedules/${id}`).then(r => r.data),
  toggle: (id: number) => apiClient.post<TaskSchedule>(`/schedules/${id}/toggle`).then(r => r.data),
  runNow: (id: number) => apiClient.post<ScheduleRunNowResult>(`/schedules/${id}/run-now`).then(r => r.data),
};

export const audit = {
  list: (
    skip = 0,
    limit = 50,
    filters?: {
      resource_type?: string;
      action?: string;
      user_id?: number;
      start_time?: string;
      end_time?: string;
    }
  ) => {
    const params: Record<string, any> = { skip, limit };
    if (filters) {
      Object.entries(filters).forEach(([k, v]) => {
        if (v !== '' && v !== undefined) params[k] = v;
      });
    }
    return apiClient.get<PaginatedResponse<any>>('/audit-logs', { params }).then(r => r.data);
  },
};
