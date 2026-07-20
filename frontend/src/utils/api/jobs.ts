import apiClient from './client';
import type { HostActiveJob } from './types';

/** Bulk occupancy snapshot for plan-execute (B1b). */
export const jobs = {
  activeByDevice: () =>
    apiClient.get<HostActiveJob[]>('/jobs/active-by-device').then(r => r.data),
};
