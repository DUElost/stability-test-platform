import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';

import { resolveRouteTitle } from '@/router/routeTitles';

/**
 * 文档标题的**唯一写入点**（#2363）。
 *
 * 旧形态有两个毛病：① 每页自己调，漏调就停留在默认值；② 多个写入者靠 effect
 * 顺序互相覆盖（父/子、清理时机都影响结果）。现在按 claim 登记 + 优先级求值：
 *
 * - `<RouteTitle/>`（根组件挂一次）登记**路由级默认**，优先级 0；
 * - 页面可选登记**动态覆盖**（传 title），优先级 1；
 * - claim 全部撤走（整体卸载）时不动文档标题——不做「快照 + restore」那套。
 *
 * 新增页面无需记得调任何东西就有标题——这正是原来的失效形态。
 */
const ROUTE_DEFAULT_PRIORITY = 0;
const PAGE_OVERRIDE_PRIORITY = 1;

interface Claim {
  title: string;
  priority: number;
  order: number;
}

const claims = new Map<object, Claim>();
let claimOrder = 0;

function applyTitle(): void {
  let best: Claim | null = null;
  for (const claim of claims.values()) {
    if (
      best === null ||
      claim.priority > best.priority ||
      (claim.priority === best.priority && claim.order > best.order)
    ) {
      best = claim;
    }
  }
  // 没有任何 claim 时**不动**文档标题。`<RouteTitle/>` 与应用同生命周期，所以这只
  // 发生在整体卸载；旧写法靠「记住 original 再 restore」收尾，那个快照取于模块
  // 加载期（测试/预加载下并不等于 index.html 的标题），是凭空多出来的脆弱依赖。
  if (best === null) return;
  document.title = `${best.title} | STP`;
}

function useTitleClaim(title: string | null | undefined, priority: number): void {
  // 每个组件实例一份稳定身份（不用 ref：react-hooks/refs 禁止 render 期取 .current，
  // 而 state 初值恰好提供「同一实例内恒定、跨实例不同」的 key）。
  const [owner] = useState<object>(() => ({}));

  useEffect(() => {
    const trimmed = (title ?? '').trim();
    if (trimmed) {
      claimOrder += 1;
      claims.set(owner, { title: trimmed, priority, order: claimOrder });
    } else {
      claims.delete(owner);
    }
    applyTitle();
    return () => {
      claims.delete(owner);
      applyTitle();
    };
  }, [owner, title, priority]);
}

/**
 * 页面标题。传参 = 覆盖路由级默认（详情页拿到数据后用）；
 * **不传 = 不登记**，由 `<RouteTitle/>` 的路由默认兜住。
 */
export function useDocumentTitle(title?: string): void {
  useTitleClaim(title, PAGE_OVERRIDE_PRIORITY);
}

/** 挂在路由根上一次：按当前路径登记默认标题（不渲染任何东西）。 */
export function RouteTitle(): null {
  const { pathname } = useLocation();
  useTitleClaim(resolveRouteTitle(pathname), ROUTE_DEFAULT_PRIORITY);
  return null;
}
