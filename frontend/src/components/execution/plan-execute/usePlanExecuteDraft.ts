import { useCallback, useEffect, useRef, useState } from 'react';
import {
  loadPlanExecuteDraft,
  removePlanExecuteDraft,
  savePlanExecuteDraft,
} from './draft';
import type { PlanExecuteDraftV2 } from './types';

/** 挂载时从 sessionStorage 读取草稿一次（用于 state 初始化）。 */
export function useInitialPlanExecuteDraft() {
  const [initialDraft] = useState(() => loadPlanExecuteDraft());
  return initialDraft;
}

interface UsePlanExecuteDraftWriterOptions {
  draft: PlanExecuteDraftV2;
}

/** 防抖写入 sessionStorage；clear 时抑制回写。 */
export function usePlanExecuteDraftWriter({ draft }: UsePlanExecuteDraftWriterOptions) {
  const suppressDraftWriteRef = useRef(false);
  const draftConsumedRef = useRef(false);

  useEffect(() => {
    if (suppressDraftWriteRef.current) return;
    const timer = window.setTimeout(() => {
      if (suppressDraftWriteRef.current) return;
      savePlanExecuteDraft(draft);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [
    draft.planId,
    draft.deviceIds,
    draft.phase,
    draft.view,
    draft.deviceFilter,
    draft.deviceVersionFilter,
    draft.deviceHostFilter,
    draft.deviceModelFilter,
    draft.deviceTagFilter,
    draft.readyOnly,
    draft,
  ]);

  const clearDraft = useCallback(() => {
    suppressDraftWriteRef.current = true;
    removePlanExecuteDraft();
  }, []);

  return { suppressDraftWriteRef, draftConsumedRef, clearDraft };
}
