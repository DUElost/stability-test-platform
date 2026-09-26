import { useQuery } from '@tanstack/react-query';
import { api, type User } from '@/utils/api';

export const authSessionKey = ['auth', 'me'] as const;

export function useAuthSession() {
  return useQuery<User>({
    queryKey: authSessionKey,
    queryFn: () => api.auth.me(),
    // #3226：给一次重试。原先完全不重试 ⇒ 单次瞬时故障即定论，配合守卫的
    // 二分支就是「抖一下就把人踢回登录页」；重试只多花一次请求，不改变
    // 真 401 的终态语义（classifyAuthFailure 仍按 status 判）。
    retry: 1,
    staleTime: 5 * 60 * 1_000,
    refetchOnWindowFocus: false,
  });
}
