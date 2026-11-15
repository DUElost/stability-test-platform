import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

export type UrlParamPatch = Record<string, string | number | boolean | null | undefined>;

/**
 * 统一的 URL 查询参数读写 Hook：
 * - 默认值/false/空串不写入 URL，保持地址栏整洁
 * - 变更筛选条件时可选同步重置 `page`（回到第 1 页）
 */
export function useUrlFilterParams() {
  const [searchParams, setSearchParams] = useSearchParams();

  const getString = useCallback(
    (key: string, fallback = '') => searchParams.get(key) ?? fallback,
    [searchParams],
  );

  const getBool = useCallback(
    (key: string) => {
      const raw = searchParams.get(key);
      return raw === '1' || raw === 'true';
    },
    [searchParams],
  );

  const getPositiveInt = useCallback(
    (key: string, fallback: number) => {
      const raw = searchParams.get(key);
      if (!raw) return fallback;
      const parsed = Number.parseInt(raw, 10);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
    },
    [searchParams],
  );

  const updateParams = useCallback(
    (
      patch: UrlParamPatch,
      options?: { defaults?: Record<string, string | number | boolean>; resetPage?: boolean; replace?: boolean },
    ) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          const defaults = options?.defaults ?? {};
          for (const [key, val] of Object.entries(patch)) {
            const def = defaults[key];
            if (
              val === undefined ||
              val === null ||
              val === '' ||
              val === false ||
              (def !== undefined && val === def)
            ) {
              next.delete(key);
            } else if (val === true) {
              next.set(key, '1');
            } else {
              next.set(key, String(val));
            }
          }
          if (options?.resetPage) {
            next.delete('page');
          }
          return next;
        },
        { replace: options?.replace ?? true },
      );
    },
    [setSearchParams],
  );

  const clearParams = useCallback(
    (keys: string[]) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const key of keys) {
            next.delete(key);
          }
          next.delete('page');
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  return {
    searchParams,
    getString,
    getBool,
    getPositiveInt,
    updateParams,
    clearParams,
  };
}
