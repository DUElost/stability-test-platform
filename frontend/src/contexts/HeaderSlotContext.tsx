import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';

interface HeaderSlotSetterCtx {
  setHeaderSlot: (node: ReactNode) => void;
  isDefault: boolean;
}

interface HeaderSlotCtx extends HeaderSlotSetterCtx {
  headerSlot: ReactNode;
}

const DEFAULT_SETTER: HeaderSlotSetterCtx = {
  setHeaderSlot: () => {},
  isDefault: true,
};

const HeaderSlotValueContext = createContext<ReactNode>(null);
const HeaderSlotSetterContext = createContext<HeaderSlotSetterCtx>(DEFAULT_SETTER);

/**
 * 顶栏槽位上下文（#4117 U1-3 读写拆分）：
 * - 写入端（`PageHeader` / `usePlanRunHeaderSlot` / `usePlanEditHeaderSlot`）只订阅
 *   `HeaderSlotSetterContext`，其 value 引用恒定，避免每次 `setHeaderSlot` 触发子页面回环重渲染；
 * - 读取端（`AppShell`）订阅 `HeaderSlotValueContext`。
 */
export function HeaderSlotProvider({ children }: { children: ReactNode }) {
  const [headerSlot, setHeaderSlot] = useState<ReactNode>(null);
  const setterValue = useMemo<HeaderSlotSetterCtx>(
    () => ({ setHeaderSlot, isDefault: false }),
    [],
  );

  return (
    <HeaderSlotSetterContext.Provider value={setterValue}>
      <HeaderSlotValueContext.Provider value={headerSlot}>
        {children}
      </HeaderSlotValueContext.Provider>
    </HeaderSlotSetterContext.Provider>
  );
}

/** 仅订阅稳定 setter 与 `isDefault` 标记，不因槽位内容变化而重渲染。 */
// eslint-disable-next-line react-refresh/only-export-components
export function useHeaderSlotSetter(): HeaderSlotSetterCtx {
  return useContext(HeaderSlotSetterContext);
}

/** 兼容入口：同时返回当前槽位节点与 setter（由 `AppShell` 等读取端消费）。 */
export function useHeaderSlot(): HeaderSlotCtx {
  const headerSlot = useContext(HeaderSlotValueContext);
  const { setHeaderSlot, isDefault } = useContext(HeaderSlotSetterContext);
  return useMemo(
    () => ({ headerSlot, setHeaderSlot, isDefault }),
    [headerSlot, setHeaderSlot, isDefault],
  );
}
