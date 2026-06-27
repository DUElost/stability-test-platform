import { createContext, useContext, useState, useCallback, useRef, type ReactNode } from 'react';

interface HeaderSlotCtx {
  /** @deprecated Use PageHeaderV2 rendered inside the page instead. */
  headerSlot: ReactNode;
  /** @deprecated Use PageHeaderV2 rendered inside the page instead. */
  setHeaderSlot: (node: ReactNode) => void;
  /** @deprecated Use PageContainer fullBleed instead. */
  fullBleed: boolean;
  /** @deprecated Use PageContainer fullBleed instead. */
  setFullBleed: (v: boolean) => void;
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
  const warnedRef = useRef(false);

  const setHeaderSlot = useCallback((n: ReactNode) => {
    if (!warnedRef.current && n !== null) {
      warnedRef.current = true;
      console.warn(
        '[HeaderSlotContext] setHeaderSlot is deprecated. Migrate to PageHeaderV2 inside the page.',
      );
    }
    setSlotRaw(n);
  }, []);

  const setFullBleed = useCallback((v: boolean) => {
    if (!warnedRef.current && v) {
      warnedRef.current = true;
      console.warn(
        '[HeaderSlotContext] setFullBleed is deprecated. Use PageContainer fullBleed instead.',
      );
    }
    setBleedRaw(v);
  }, []);

  return (
    <Ctx.Provider value={{ headerSlot, setHeaderSlot, fullBleed, setFullBleed, isDefault: false }}>
      {children}
    </Ctx.Provider>
  );
}

export function useHeaderSlot() {
  return useContext(Ctx);
}
