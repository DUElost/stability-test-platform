import { createContext, useContext, useState, type ReactNode } from 'react';

interface HeaderSlotCtx {
  headerSlot: ReactNode;
  setHeaderSlot: (node: ReactNode) => void;
  isDefault: boolean;
}

const Ctx = createContext<HeaderSlotCtx>({
  headerSlot: null,
  setHeaderSlot: () => {},
  isDefault: true,
});

export function HeaderSlotProvider({ children }: { children: ReactNode }) {
  const [headerSlot, setHeaderSlot] = useState<ReactNode>(null);

  return (
    <Ctx.Provider value={{ headerSlot, setHeaderSlot, isDefault: false }}>
      {children}
    </Ctx.Provider>
  );
}

export function useHeaderSlot() {
  return useContext(Ctx);
}
