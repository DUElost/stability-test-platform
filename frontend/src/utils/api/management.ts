import apiClient from './client';
import type {
  User, NotificationChannel, AlertRule, TaskSchedule, TaskScheduleCreatePayload,
  TaskScheduleUpdatePayload, ScheduleRunNowResult, PaginatedResponse,
} from './types';

export const users = {
  list: (skip = 0, limit = 50) => apiClient.get<PaginatedResponse<User>>('/users', { params: { skip, limit } }),
  get: (id: number) => apiClient.get<User>(`/users/${id}`),
  create: (data: { username: string; password: string; role: string }) =>
    apiClient.post<User>('/users', data),
  update: (id: number, data: { username?: string; password?: string; role?: string; is_active?: string }) =>
    apiClient.put<User>(`/users/${id}`, data),
  delete: (id: number) => apiClient.delete<void>(`/users/${id}`),
  toggleActive: (id: number) => apiClient.post<User>(`/users/${id}/toggle-active`),
  changePassword: (data: { old_password: string; new_password: string }) =>
    apiClient.post('/users/change-password', data),
};

export const notifications = {
  listChannels: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<NotificationChannel>>('/notifications/channels', { params: { skip, limit } }),
  createChannel: (data: { name: string; type: string; config: Record<string, any>; enabled?: boolean }) =>
    apiClient.post<NotificationChannel>('/notifications/channels', data),
  updateChannel: (id: number, data: Partial<{ name: string; type: string; config: Record<string, any>; enabled: boolean }>) =>
    apiClient.put<NotificationChannel>(`/notifications/channels/${id}`, data),
  deleteChannel: (id: number) => apiClient.delete<void>(`/notifications/channels/${id}`),
  testChannel: (id: number) =>
    apiClient.post<{ ok: boolean; message: string }>(`/notifications/channels/${id}/test`),
  listRules: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<AlertRule>>('/notifications/rules', { params: { skip, limit } }),
  createRule: (data: { name: string; event_type: string; channel_id: number; filters?: Record<string, any>; enabled?: boolean }) =>
    apiClient.post<AlertRule>('/notifications/rules', data),
  updateRule: (id: number, data: Partial<{ name: string; event_type: string; channel_id: number; filters: Record<string, any>; enabled: boolean }>) =>
    apiClient.put<AlertRule>(`/notifications/rules/${id}`, data),
  deleteRule: (id: number) => apiClient.delete<void>(`/notifications/rules/${id}`),
};

export const schedules = {
  list: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<TaskSchedule>>('/schedules', { params: { skip, limit } }),
  get: (id: number) => apiClient.get<TaskSchedule>(`/schedules/${id}`),
  create: (data: TaskScheduleCreatePayload) => apiClient.post<TaskSchedule>('/schedules', data),
  update: (id: number, data: TaskScheduleUpdatePayload) => apiClient.put<TaskSchedule>(`/schedules/${id}`, data),
  delete: (id: number) => apiClient.delete<void>(`/schedules/${id}`),
  toggle: (id: number) => apiClient.post<TaskSchedule>(`/schedules/${id}/toggle`),
  runNow: (id: number) => apiClient.post<ScheduleRunNowResult>(`/schedules/${id}/run-now`),
};

export const templates = {
  list: (skip = 0, limit = 50) =>
    apiClient.get<PaginatedResponse<any>>('/templates', { params: { skip, limit } }),
  get: (id: number) => apiClient.get<any>(`/templates/${id}`),
  create: (data: any) => apiClient.post<any>('/templates', data),
  update: (id: number, data: any) => apiClient.put<any>(`/templates/${id}`, data),
  delete: (id: number) => apiClient.delete<void>(`/templates/${id}`),
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
    return apiClient.get<PaginatedResponse<any>>('/audit-logs', { params });
  },
};
