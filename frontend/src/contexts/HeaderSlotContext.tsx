import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

interface HeaderSlotCtx {
  /** 注入到 AppShell 顶栏左侧的内容节点 */
  headerSlot: ReactNode;
  setHeaderSlot: (node: ReactNode) => void;
  /** true 时 AppShell main 去掉内边距并锁定 overflow，适合需要自管滚动的全屏页面 */
  fullBleed: boolean;
  setFullBleed: (v: boolean) => void;
  /** 默认 context(无 Provider 包裹,如单元测试)为 true;真实 AppShell 内为 false。
   *  消费方据此决定把内容注入顶栏,还是回退到自身渲染。 */
  isDefault: boolean;
}

const Ctx = createContext<HeaderSlotCtx>({
  headerSlot: null,
  setHeaderSlot: () => {},
  fullBleed: false,
  setFullBleed: () => {},
  isDefault: true,
});

export function HeaderSlotProvider({ children }: { children: ReactNode }) {
  const [headerSlot, setSlotRaw] = useState<ReactNode>(null);
  const [fullBleed, setBleedRaw] = useState(false);
  const setHeaderSlot = useCallback((n: ReactNode) => setSlotRaw(n), []);
  const setFullBleed = useCallback((v: boolean) => setBleedRaw(v), []);
  return (
    <Ctx.Provider value={{ headerSlot, setHeaderSlot, fullBleed, setFullBleed, isDefault: false }}>
      {children}
    </Ctx.Provider>
  );
}

export function useHeaderSlot() {
  return useContext(Ctx);
}
