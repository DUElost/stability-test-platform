import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

interface HeaderSlotCtx {
  /** 注入到 AppShell 顶栏左侧的内容节点 */
  headerSlot: ReactNode;
  setHeaderSlot: (node: ReactNode) => void;
  /** true 时 AppShell main 去掉内边距并锁定 overflow，适合需要自管滚动的全屏页面 */
  fullBleed: boolean;
  setFullBleed: (v: boolean) => void;
}

const Ctx = createContext<HeaderSlotCtx>({
  headerSlot: null,
  setHeaderSlot: () => {},
  fullBleed: false,
  setFullBleed: () => {},
});

export function HeaderSlotProvider({ children }: { children: ReactNode }) {
  const [headerSlot, setSlotRaw] = useState<ReactNode>(null);
  const [fullBleed, setBleedRaw] = useState(false);
  const setHeaderSlot = useCallback((n: ReactNode) => setSlotRaw(n), []);
  const setFullBleed = useCallback((v: boolean) => setBleedRaw(v), []);
  return (
    <Ctx.Provider value={{ headerSlot, setHeaderSlot, fullBleed, setFullBleed }}>
      {children}
    </Ctx.Provider>
  );
}

export function useHeaderSlot() {
  return useContext(Ctx);
}
