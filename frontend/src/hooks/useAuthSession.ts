import { useQuery } from '@tanstack/react-query';
import { api, type User } from '@/utils/api';

export const authSessionKey = ['auth', 'me'] as const;

export function useAuthSession() {
  return useQuery<User>({
    queryKey: authSessionKey,
    queryFn: () => api.auth.me(),
    retry: false,
    staleTime: 5 * 60 * 1_000,
    refetchOnWindowFocus: false,
  });
}
