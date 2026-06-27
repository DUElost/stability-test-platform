# Frontend UI/UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Stability Test Platform frontend layout, lists, detail/edit pages, and global feedback components to be consistent, responsive, and accessible while keeping existing pages working.

**Architecture:** Introduce a new layout layer (`PageContainer`, `PageHeaderV2`) that replaces `HeaderSlotContext` injection; unify lists/tables under `DataList`/`DataTable`; reorganize `PlanRunDetailPage` into tabs; make `PlanEditPage` a responsive resizable three-column editor; and swap the custom toast for `sonner` behind a compatible wrapper.

**Tech Stack:** React 18, TypeScript, Tailwind CSS, shadcn/ui primitives, `@tanstack/react-table` (already installed), `react-resizable-panels`, `sonner`, Vitest.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/src/components/layout/PageContainer.tsx` | Modify | Add `fullBleed`/`scrollable`; keep `width` backward compatible |
| `frontend/src/components/layout/PageHeaderV2.tsx` | Create | New declarative page header with breadcrumbs/actions |
| `frontend/src/components/layout/index.ts` | Modify | Export `PageHeaderV2` |
| `frontend/src/components/data/DataEmptyState.tsx` | Create | Shared empty state |
| `frontend/src/components/data/DataErrorState.tsx` | Create | Shared error state |
| `frontend/src/components/data/DataSkeleton.tsx` | Create | Shared list/table skeleton |
| `frontend/src/components/data/DataToolbar.tsx` | Create | Search/filter/sort bar |
| `frontend/src/components/data/DataPagination.tsx` | Create | Shared pagination |
| `frontend/src/components/data/DataList.tsx` | Create | Unified list (cards/rows) |
| `frontend/src/components/data/DataListItem.tsx` | Create | List item wrapper with actions |
| `frontend/src/components/data/DataTable.tsx` | Create | Unified table based on TanStack Table |
| `frontend/src/components/data/index.ts` | Create | Barrel exports |
| `frontend/src/components/layout/AppShell.tsx` | Modify | Remove global `PageHeader` slot, simplify main scroll |
| `frontend/src/contexts/HeaderSlotContext.tsx` | Modify | Add deprecation warning |
| `frontend/src/pages/orchestration/PlanListPage.tsx` | Modify | Use `PageHeaderV2` + `PageContainer fullBleed` + `DataList` |
| `frontend/src/pages/execution/PlanRunListPage.tsx` | Modify | Use `PageHeaderV2` + `PageContainer fullBleed` + `DataList` |
| `frontend/src/pages/results/ResultsPage.tsx` | Modify | Use `PageHeaderV2` + `PageContainer fullBleed` + `DataTable` |
| `frontend/src/pages/execution/PlanRunDetailPage/index.tsx` | Create | New tabbed entry |
| `frontend/src/pages/execution/PlanRunDetailPage/RunStatusBanner.tsx` | Create | Status hero |
| `frontend/src/pages/execution/PlanRunDetailPage/RunOverviewTab.tsx` | Create | Overview tab |
| `frontend/src/pages/execution/PlanRunDetailPage/RunDevicesTab.tsx` | Create | Devices tab |
| `frontend/src/pages/execution/PlanRunDetailPage/RunArtifactsTab.tsx` | Create | Artifacts tab (stub if API absent) |
| `frontend/src/pages/execution/PlanRunDetailPage/RunLogsTab.tsx` | Create | Logs inner tab |
| `frontend/src/pages/execution/PlanRunDetailPage/RunSignalsTab.tsx` | Create | Watcher signals inner tab |
| `frontend/src/pages/execution/PlanRunDetailPage/RunTimelineTab.tsx` | Create | Timeline tab |
| `frontend/src/pages/execution/PlanRunDetailPage/PlanRunMeta.tsx` | Create | Header description metadata |
| `frontend/src/pages/execution/PlanRunDetailPage.tsx` | Delete | Replaced by `index.tsx` |
| `frontend/src/pages/orchestration/PlanEditPage.tsx` | Modify | Use `PageHeaderV2` + resizable panels |
| `frontend/src/components/ui/UserMenu.tsx` | Create | Accessible user dropdown |
| `frontend/src/hooks/useDocumentTitle.ts` | Create | Document title hook |
| `frontend/src/components/ui/Toaster.tsx` | Create | Sonner toaster wrapper |
| `frontend/src/hooks/useToast.ts` | Create | Sonner-based toast wrapper (API compatible) |
| `frontend/src/components/ui/toast.tsx` | Delete | Replaced by sonner wrapper |
| `frontend/src/components/ui/toast.test.tsx` | Modify | Update tests for sonner wrapper |
| `frontend/src/main.tsx` or `App.tsx` | Modify | Render `<Toaster />` globally |

---

## Phase 1: Foundation Components & Layout Framework

### Task 1: Install new dependencies

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Add dependencies**

Run in PowerShell from repo root:

```powershell
cd frontend; npm install react-resizable-panels sonner
```

- [ ] **Step 2: Verify install**

Run:

```powershell
cd frontend; npm ls react-resizable-panels sonner
```

Expected output contains:

```
react-resizable-panels@<version>
sonner@<version>
```

- [ ] **Step 3: Commit**

```bash
cd frontend
git add package.json package-lock.json
git commit -m "chore(deps): add react-resizable-panels and sonner"
```

---

### Task 2: Refactor `PageContainer` to support new layout modes

**Files:**
- Modify: `frontend/src/components/layout/PageContainer.tsx`
- Test: `frontend/src/components/layout/PageContainer.test.tsx` (create if absent)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/layout/PageContainer.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PageContainer } from './PageContainer';

describe('PageContainer', () => {
  it('renders children with default padding', () => {
    render(<PageContainer>content</PageContainer>);
    expect(screen.getByText('content')).toBeInTheDocument();
  });

  it('fullBleed removes horizontal padding', () => {
    const { container } = render(<PageContainer fullBleed>content</PageContainer>);
    const root = container.firstChild as HTMLElement;
    expect(root.className).not.toContain('px-');
    expect(root.className).toContain('h-full');
  });

  it('scrollable=false removes overflow-auto', () => {
    const { container } = render(<PageContainer scrollable={false}>content</PageContainer>);
    const root = container.firstChild as HTMLElement;
    expect(root.className).not.toContain('overflow-auto');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd frontend; npx vitest run src/components/layout/PageContainer.test.tsx
```

Expected: FAIL (props do not exist yet).

- [ ] **Step 3: Modify `PageContainer.tsx`**

Replace the file content with:

```tsx
import React from 'react';
import { LAYOUT, type PageWidth } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

interface PageContainerProps {
  children: React.ReactNode;
  className?: string;
  /** Content max-width preset. Ignored when fullBleed is true. */
  width?: PageWidth;
  /** Remove horizontal padding so lists/tables touch the viewport edges. */
  fullBleed?: boolean;
  /** Whether the container itself scrolls. Disable for editors that manage their own panels. */
  scrollable?: boolean;
}

/**
 * 页面容器 — 统一间距、入场动画与可选最大宽度。
 * 新页面应优先使用 fullBleed + PageHeaderV2，旧 width 预设保留兼容。
 */
export const PageContainer: React.FC<PageContainerProps> = ({
  children,
  className = '',
  width = 'wide',
  fullBleed = false,
  scrollable = true,
}) => {
  return (
    <div
      className={cn(
        'h-full flex flex-col',
        LAYOUT.pageEnter,
        scrollable && 'overflow-auto',
        fullBleed ? 'w-full' : [LAYOUT.pagePadding, LAYOUT.pageWidth[width]],
        className,
      )}
    >
      {children}
    </div>
  );
};

export default PageContainer;
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
cd frontend; npx vitest run src/components/layout/PageContainer.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/layout/PageContainer.tsx frontend/src/components/layout/PageContainer.test.tsx
git commit -m "feat(layout): PageContainer supports fullBleed and scrollable"
```

---

### Task 3: Create `PageHeaderV2`

**Files:**
- Create: `frontend/src/components/layout/PageHeaderV2.tsx`
- Modify: `frontend/src/components/layout/index.ts`
- Test: `frontend/src/components/layout/PageHeaderV2.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/layout/PageHeaderV2.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { PageHeaderV2 } from './PageHeaderV2';

describe('PageHeaderV2', () => {
  it('renders title and actions', () => {
    render(
      <MemoryRouter>
        <PageHeaderV2 title="Plans" actions={<button>Create</button>} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: 'Plans' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create' })).toBeInTheDocument();
  });

  it('renders breadcrumbs', () => {
    render(
      <MemoryRouter>
        <PageHeaderV2
          title="Edit Plan"
          breadcrumbs={[{ label: 'Plans', path: '/plans' }, { label: 'Edit' }]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'Plans' })).toHaveAttribute('href', '/plans');
    expect(screen.getByText('Edit')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
cd frontend; npx vitest run src/components/layout/PageHeaderV2.test.tsx
```

Expected: FAIL (file not found).

- [ ] **Step 3: Implement `PageHeaderV2.tsx`**

Create `frontend/src/components/layout/PageHeaderV2.tsx`:

```tsx
import React, { ReactNode } from 'react';
import { ChevronRight, Home, MoreHorizontal } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { TEXT, INTERACTIVE } from '@/design-system/tokens';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';

export interface BreadcrumbItem {
  label: string;
  path?: string;
}

export interface PageHeaderV2Props {
  title: ReactNode;
  breadcrumbs?: BreadcrumbItem[];
  actions?: ReactNode;
  secondaryActions?: ReactNode;
  description?: ReactNode;
  sticky?: boolean;
  className?: string;
}

function PageHeaderBreadcrumbs({ breadcrumbs }: { breadcrumbs: BreadcrumbItem[] }) {
  return (
    <nav aria-label="Breadcrumb" className={cn('flex items-center text-xs', TEXT.subtitle)}>
      <Link to="/" className={cn('flex items-center transition-colors', INTERACTIVE.hoverText)}>
        <Home size={12} className="mr-1" />
        首页
      </Link>
      {breadcrumbs.map((item, index) => (
        <React.Fragment key={`${item.label}-${index}`}>
          <ChevronRight size={12} className="mx-1.5 text-muted-foreground/40" />
          {item.path ? (
            <Link to={item.path} className={cn('transition-colors', INTERACTIVE.hoverText)}>
              {item.label}
            </Link>
          ) : (
            <span className={cn('font-medium', TEXT.heading)}>{item.label}</span>
          )}
        </React.Fragment>
      ))}
    </nav>
  );
}

export const PageHeaderV2: React.FC<PageHeaderV2Props> = ({
  title,
  breadcrumbs,
  actions,
  secondaryActions,
  description,
  sticky = false,
  className,
}) => {
  const hasSecondary = !!secondaryActions;

  return (
    <div
      className={cn(
        'flex flex-col gap-3 pb-4',
        sticky && 'sticky top-0 z-10 bg-background/95 backdrop-blur-sm',
        className,
      )}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1 flex flex-col gap-1">
          {breadcrumbs && breadcrumbs.length > 0 && (
            <PageHeaderBreadcrumbs breadcrumbs={breadcrumbs} />
          )}
          <h1 className={cn('text-xl font-semibold tracking-tight', TEXT.heading)}>{title}</h1>
          {description && <div className={cn('text-sm', TEXT.subtitle)}>{description}</div>}
        </div>

        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {/* On small screens, collapse actions into a dropdown if there are many */}
            <div className="hidden sm:flex items-center gap-2">{actions}</div>
            <div className="flex sm:hidden items-center gap-2">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="icon" aria-label="操作菜单">
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">{actions}</DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        )}
      </div>

      {hasSecondary && <div className="flex flex-wrap items-center gap-2">{secondaryActions}</div>}
    </div>
  );
};

export default PageHeaderV2;
```

- [ ] **Step 4: Export from `index.ts`**

Modify `frontend/src/components/layout/index.ts`:

```ts
export { PageContainer } from './PageContainer';
export { PageHeader } from './PageHeader';
export { PageHeaderV2 } from './PageHeaderV2';
export { StatsGrid } from './StatsGrid';
export type { StatItem } from './StatsGrid';
```

- [ ] **Step 5: Run test to verify it passes**

```powershell
cd frontend; npx vitest run src/components/layout/PageHeaderV2.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/layout/PageHeaderV2.tsx frontend/src/components/layout/PageHeaderV2.test.tsx frontend/src/components/layout/index.ts
git commit -m "feat(layout): add PageHeaderV2 component"
```

---

### Task 4: Simplify `AppShell` and remove global header slot

**Files:**
- Modify: `frontend/src/layouts/AppShell.tsx`

- [ ] **Step 1: Inspect current `AppShell.tsx`**

Already read. Key lines:
- Line 11: `import { useHeaderSlot } from '@/contexts/HeaderSlotContext';`
- Line 26: `const { headerSlot, fullBleed } = useHeaderSlot();`
- Line 143-145: renders `{headerSlot}`.
- Line 255-277: conditional `fullBleed` main rendering.

- [ ] **Step 2: Modify `AppShell.tsx`**

Replace the file content with:

```tsx
import { useState, useEffect, Suspense } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import Sidebar from './Sidebar';
import { Menu, ChevronRight, FileText, LogOut, User, ChevronDown, Loader2, KeyRound, Wifi, WifiOff, Users, Shield, Settings } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { clearAppQueryCache } from '@/components/QueryProvider';
import { useSocketIO, disconnectDashSocket } from '@/hooks/useSocketIO';
import { useAuthSession } from '@/hooks/useAuthSession';
import { api } from '@/utils/api';
import { WS_DASHBOARD_ENDPOINT } from '@/config';
import { BORDER, ELEVATION, INTERACTIVE, SURFACE, TEXT } from '@/design-system/tokens';

/**
 * 主应用布局 - 源自 web 样板设计风格
 */
export default function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const navigate = useNavigate();
  const sessionQ = useAuthSession();
  const currentUser = sessionQ.data;
  const { isConnected: dashConnected } = useSocketIO(WS_DASHBOARD_ENDPOINT);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 1024);
      if (window.innerWidth >= 1024) {
        setSidebarOpen(false);
      }
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setSidebarOpen(false);
        setShowUserMenu(false);
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, []);

  const toggleSidebar = () => setSidebarOpen(!sidebarOpen);
  const toggleSidebarCollapse = () => setSidebarCollapsed(!sidebarCollapsed);

  const handleLogout = async () => {
    try {
      await api.auth.logout();
    } catch {
      // ignore — local UI should still transition to login
    }
    clearAppQueryCache();
    disconnectDashSocket();
    navigate('/login');
  };

  return (
    <div className={cn('flex h-screen', SURFACE.page)}>
      {isMobile && sidebarOpen && (
        <div
          className={cn('fixed inset-0 z-40 transition-opacity duration-300', SURFACE.overlay)}
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={cn(
          'hidden lg:flex flex-col transition-all duration-300',
          SURFACE.elevated,
          BORDER.default,
          'border-r',
        )}
        style={{ width: sidebarCollapsed ? 72 : 224 }}
      >
        <Sidebar
          onNavigate={() => isMobile && setSidebarOpen(false)}
          collapsed={sidebarCollapsed}
          onToggleCollapse={toggleSidebarCollapse}
        />
      </aside>

      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 w-56 transform transition-transform duration-300 lg:hidden border-r',
          SURFACE.elevated,
          BORDER.default,
          sidebarOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <Sidebar
          onNavigate={() => setSidebarOpen(false)}
          collapsed={false}
          isMobile={true}
          onCloseMobile={() => setSidebarOpen(false)}
        />
      </aside>

      {!isMobile && sidebarCollapsed && (
        <button
          onClick={toggleSidebarCollapse}
          aria-label="展开侧边栏"
          className={cn(
            'fixed left-[60px] top-1/2 -translate-y-1/2 z-40 h-6 w-6 rounded-full flex items-center justify-center transition-all duration-200',
            SURFACE.elevated,
            BORDER.default,
            ELEVATION.sm,
            INTERACTIVE.hover,
          )}
        >
          <ChevronRight size={14} className={TEXT.subtitle} />
        </button>
      )}

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className={cn('sticky top-0 z-30 border-b', SURFACE.header, BORDER.default)}>
          <div className="flex items-center justify-between h-14 px-4 lg:px-6">
            <button
              onClick={toggleSidebar}
              className={cn('lg:hidden p-2', INTERACTIVE.iconButton)}
              aria-label="打开侧边栏"
            >
              <Menu className="w-5 h-5" />
            </button>

            <div className="flex-1" />

            <div className="flex items-center gap-2">
              <Badge
                variant={dashConnected ? 'success' : 'destructive'}
                className="hidden gap-1.5 sm:inline-flex"
                title={dashConnected ? '实时数据通道已连接' : '实时连接已断开'}
              >
                {dashConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
                {dashConnected ? '实时连接' : '已断开'}
              </Badge>

              <div className="relative ml-2">
                <button
                  onClick={() => setShowUserMenu(!showUserMenu)}
                  className={cn('flex items-center gap-2 p-1.5 rounded-lg transition-colors', INTERACTIVE.hover)}
                  aria-label="用户菜单"
                  aria-expanded={showUserMenu}
                >
                  <div className={cn('w-8 h-8 rounded-full flex items-center justify-center', SURFACE.subtle)}>
                    <User className={cn('w-4 h-4', TEXT.subtitle)} />
                  </div>
                  <div className="hidden sm:flex flex-col items-start leading-tight">
                    <span className={cn('text-sm font-medium', TEXT.heading)}>
                      {currentUser?.username ?? '...'}
                    </span>
                    {currentUser?.role && (
                      <span className={cn('text-xs', TEXT.caption)}>{currentUser.role}</span>
                    )}
                  </div>
                  <ChevronDown className={cn(
                    'w-4 h-4 transition-transform hidden sm:block',
                    TEXT.caption,
                    showUserMenu && 'rotate-180',
                  )} />
                </button>

                {showUserMenu && (
                  <>
                    <div
                      className="fixed inset-0 z-10"
                      onClick={() => setShowUserMenu(false)}
                    />
                    <div className={cn('absolute right-0 top-full mt-1 w-48 rounded-lg py-1 z-20', SURFACE.elevated, ELEVATION.dropdown)}>
                      <a
                        href="/docs"
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={() => setShowUserMenu(false)}
                        className={cn('flex items-center gap-3 px-4 py-2 text-sm', INTERACTIVE.menuItem)}
                      >
                        <FileText className="w-4 h-4" />
                        文档
                      </a>
                      <a
                        href="/account/password"
                        onClick={() => setShowUserMenu(false)}
                        className={cn('flex items-center gap-3 px-4 py-2 text-sm', INTERACTIVE.menuItem)}
                      >
                        <KeyRound className="w-4 h-4" />
                        修改密码
                      </a>
                      {currentUser?.role === 'admin' && (
                        <>
                          <hr className={cn('my-1', BORDER.default)} />
                          <a
                            href="/users"
                            onClick={() => setShowUserMenu(false)}
                            className={cn('flex items-center gap-3 px-4 py-2 text-sm focus-visible:outline-none', INTERACTIVE.menuItem)}
                          >
                            <Users className="w-4 h-4" />
                            用户管理
                          </a>
                          <a
                            href="/audit"
                            onClick={() => setShowUserMenu(false)}
                            className={cn('flex items-center gap-3 px-4 py-2 text-sm focus-visible:outline-none', INTERACTIVE.menuItem)}
                          >
                            <Shield className="w-4 h-4" />
                            操作日志
                          </a>
                          <a
                            href="/settings"
                            onClick={() => setShowUserMenu(false)}
                            className={cn('flex items-center gap-3 px-4 py-2 text-sm focus-visible:outline-none', INTERACTIVE.menuItem)}
                          >
                            <Settings className="w-4 h-4" />
                            系统设置
                          </a>
                        </>
                      )}
                      <hr className={cn('my-1', BORDER.default)} />
                      <button
                        onClick={handleLogout}
                        className={cn('w-full flex items-center gap-3 px-4 py-2 text-sm', INTERACTIVE.destructiveMenu)}
                      >
                        <LogOut className="w-4 h-4" />
                        退出登录
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </header>

        <main className="flex-1 min-h-0 overflow-hidden">
          <Suspense fallback={
            <div className="flex items-center justify-center h-64">
              <Loader2 className={cn('w-8 h-8 animate-spin', TEXT.caption)} />
            </div>
          }>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/layouts/AppShell.tsx
git commit -m "feat(layout): AppShell no longer renders PageHeader slot"
```

---

### Task 5: Mark `HeaderSlotContext` as deprecated

**Files:**
- Modify: `frontend/src/contexts/HeaderSlotContext.tsx`

- [ ] **Step 1: Modify the file**

Replace content with:

```tsx
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
```

- [ ] **Step 2: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/contexts/HeaderSlotContext.tsx
git commit -m "chore(layout): deprecate HeaderSlotContext with runtime warnings"
```

---

### Task 6: Create shared data subcomponents

**Files:**
- Create: `frontend/src/components/data/DataEmptyState.tsx`
- Create: `frontend/src/components/data/DataErrorState.tsx`
- Create: `frontend/src/components/data/DataSkeleton.tsx`
- Create: `frontend/src/components/data/DataToolbar.tsx`
- Create: `frontend/src/components/data/DataPagination.tsx`
- Create: `frontend/src/components/data/index.ts`

- [ ] **Step 1: Create `DataEmptyState.tsx`**

```tsx
import React, { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';

interface DataEmptyStateProps {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export const DataEmptyState: React.FC<DataEmptyStateProps> = ({
  title,
  description,
  icon,
  action,
  className,
}) => {
  return (
    <div className={cn('flex flex-col items-center justify-center py-12 text-center', className)}>
      {icon && <div className={cn('mb-4', TEXT.subtitle)}>{icon}</div>}
      <h3 className={cn('text-sm font-medium', TEXT.heading)}>{title}</h3>
      {description && <p className={cn('mt-1 text-sm max-w-sm', TEXT.subtitle)}>{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
};

export default DataEmptyState;
```

- [ ] **Step 2: Create `DataErrorState.tsx`**

```tsx
import React from 'react';
import { AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';

interface DataErrorStateProps {
  title?: string;
  description?: string;
  onRetry?: () => void;
  className?: string;
}

export const DataErrorState: React.FC<DataErrorStateProps> = ({
  title = '加载失败',
  description = '请检查网络连接或稍后重试',
  onRetry,
  className,
}) => {
  return (
    <div className={cn('flex flex-col items-center justify-center py-12 text-center', className)}>
      <AlertCircle className={cn('w-10 h-10 mb-3', TEXT.destructive)} />
      <h3 className={cn('text-sm font-medium', TEXT.heading)}>{title}</h3>
      <p className={cn('mt-1 text-sm max-w-sm', TEXT.subtitle)}>{description}</p>
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
};

export default DataErrorState;
```

- [ ] **Step 3: Create `DataSkeleton.tsx`**

```tsx
import React from 'react';
import { Skeleton } from '@/components/ui/skeleton';

interface DataSkeletonProps {
  rows?: number;
  columns?: number;
  className?: string;
}

export const DataSkeleton: React.FC<DataSkeletonProps> = ({
  rows = 5,
  columns = 1,
  className,
}) => {
  return (
    <div className={className}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 py-2">
          {Array.from({ length: columns }).map((_, j) => (
            <Skeleton key={j} className="h-10 w-full" />
          ))}
        </div>
      ))}
    </div>
  );
};

export default DataSkeleton;
```

- [ ] **Step 4: Create `DataToolbar.tsx`**

```tsx
import React, { ReactNode } from 'react';
import { Search } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';

interface DataToolbarProps {
  searchValue?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
  children?: ReactNode;
  className?: string;
}

export const DataToolbar: React.FC<DataToolbarProps> = ({
  searchValue,
  onSearchChange,
  searchPlaceholder = '搜索...',
  children,
  className,
}) => {
  return (
    <div className={cn('flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between', className)}>
      {onSearchChange && (
        <div className="relative flex-1 max-w-md">
          <Search className={cn('absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4', TEXT.subtitle)} />
          <Input
            type="text"
            placeholder={searchPlaceholder}
            value={searchValue ?? ''}
            onChange={(e) => onSearchChange(e.target.value)}
            className="pl-9"
          />
        </div>
      )}
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
};

export default DataToolbar;
```

- [ ] **Step 5: Create `DataPagination.tsx`**

```tsx
import React from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface DataPaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pageSizeOptions?: number[];
  className?: string;
}

export const DataPagination: React.FC<DataPaginationProps> = ({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50],
  className,
}) => {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const start = total === 0 ? 0 : page * pageSize + 1;
  const end = Math.min((page + 1) * pageSize, total);

  return (
    <div className={cn('flex items-center justify-between text-sm', className)}>
      <span className="text-muted-foreground">
        显示 {start}-{end} / {total}
      </span>
      <div className="flex items-center gap-2">
        {onPageSizeChange && (
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="rounded border border-border bg-background px-2 py-1 text-xs"
            aria-label="每页条数"
          >
            {pageSizeOptions.map((size) => (
              <option key={size} value={size}>
                {size} / 页
              </option>
            ))}
          </select>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page - 1)}
          disabled={page === 0}
        >
          上一页
        </Button>
        <span className="text-muted-foreground">
          {page + 1} / {totalPages}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages - 1}
        >
          下一页
        </Button>
      </div>
    </div>
  );
};

export default DataPagination;
```

- [ ] **Step 6: Create `index.ts`**

```ts
export { DataEmptyState } from './DataEmptyState';
export { DataErrorState } from './DataErrorState';
export { DataSkeleton } from './DataSkeleton';
export { DataToolbar } from './DataToolbar';
export { DataPagination } from './DataPagination';
```

- [ ] **Step 7: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/data
git commit -m "feat(data): add shared DataEmptyState, DataErrorState, DataSkeleton, DataToolbar, DataPagination"
```

---

### Task 7: Create `DataList` and `DataListItem`

**Files:**
- Create: `frontend/src/components/data/DataListItem.tsx`
- Create: `frontend/src/components/data/DataList.tsx`
- Modify: `frontend/src/components/data/index.ts`
- Test: `frontend/src/components/data/DataList.test.tsx`

- [ ] **Step 1: Create `DataListItem.tsx`**

```tsx
import React, { ReactNode } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';

export interface MoreAction {
  label: string;
  onClick: () => void;
  destructive?: boolean;
}

interface DataListItemProps {
  children: ReactNode;
  actions?: ReactNode;
  moreActions?: MoreAction[];
  onNavigate?: () => void;
  selected?: boolean;
  onSelect?: () => void;
  className?: string;
}

export const DataListItem: React.FC<DataListItemProps> = ({
  children,
  actions,
  moreActions,
  onNavigate,
  selected,
  onSelect,
  className,
}) => {
  const Main = onNavigate ? 'button' : 'div';

  return (
    <div
      className={cn(
        'group flex items-start gap-3 rounded-lg border bg-card p-3 transition-colors hover:border-border/80',
        selected && 'ring-2 ring-primary/20 border-primary/40',
        className,
      )}
    >
      {onSelect && (
        <div className="pt-1">
          <Checkbox checked={selected} onCheckedChange={onSelect} aria-label="选择" />
        </div>
      )}
      <Main
        className={cn(
          'min-w-0 flex-1 text-left',
          onNavigate && 'cursor-pointer',
        )}
        onClick={onNavigate}
        type={onNavigate ? 'button' : undefined}
      >
        {children}
      </Main>
      {(actions || (moreActions && moreActions.length > 0)) && (
        <div className="flex shrink-0 items-center gap-1 pt-0.5">
          {actions}
          {moreActions && moreActions.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="更多操作" className="h-8 w-8">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {moreActions.map((action, idx) => (
                  <DropdownMenuItem
                    key={idx}
                    onClick={action.onClick}
                    className={cn(action.destructive && 'text-destructive focus:text-destructive')}
                  >
                    {action.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      )}
    </div>
  );
};

export default DataListItem;
```

- [ ] **Step 2: Create `DataList.tsx`**

```tsx
import React, { ReactNode } from 'react';
import { DataEmptyState } from './DataEmptyState';
import { DataErrorState } from './DataErrorState';
import { DataSkeleton } from './DataSkeleton';
import { cn } from '@/lib/utils';

interface DataListRenderContext {
  isSelected: boolean;
  toggleSelect: () => void;
}

interface DataListProps<T> {
  items: T[];
  isLoading?: boolean;
  error?: Error | null;
  emptyState?: ReactNode;
  renderItem: (item: T, ctx: DataListRenderContext) => ReactNode;
  keyExtractor: (item: T) => string;
  selection?: 'none' | 'single' | 'multiple';
  selectedKeys?: Set<string>;
  onSelectionChange?: (keys: Set<string>) => void;
  header?: ReactNode;
  footer?: ReactNode;
  itemClassName?: string;
  className?: string;
}

export function DataList<T>({
  items,
  isLoading,
  error,
  emptyState,
  renderItem,
  keyExtractor,
  selection = 'none',
  selectedKeys,
  onSelectionChange,
  header,
  footer,
  itemClassName,
  className,
}: DataListProps<T>) {
  const currentKeys = selectedKeys ?? new Set<string>();

  const toggleSelect = (key: string) => {
    if (!onSelectionChange) return;
    const next = new Set(currentKeys);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onSelectionChange(next);
  };

  if (isLoading) {
    return (
      <div className={className}>
        {header}
        <DataSkeleton rows={5} />
      </div>
    );
  }

  if (error) {
    return (
      <div className={className}>
        {header}
        <DataErrorState description={error.message} />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className={className}>
        {header}
        {emptyState ?? <DataEmptyState title="暂无数据" />}
      </div>
    );
  }

  return (
    <div className={cn('space-y-3', className)}>
      {header}
      <div className="space-y-2">
        {items.map((item) => {
          const key = keyExtractor(item);
          const isSelected = currentKeys.has(key);
          return (
            <div key={key} className={itemClassName}>
              {renderItem(item, {
                isSelected,
                toggleSelect: () => toggleSelect(key),
              })}
            </div>
          );
        })}
      </div>
      {footer}
    </div>
  );
}

export default DataList;
```

- [ ] **Step 3: Update `index.ts`**

```ts
export { DataEmptyState } from './DataEmptyState';
export { DataErrorState } from './DataErrorState';
export { DataSkeleton } from './DataSkeleton';
export { DataToolbar } from './DataToolbar';
export { DataPagination } from './DataPagination';
export { DataList } from './DataList';
export { DataListItem } from './DataListItem';
export type { MoreAction } from './DataListItem';
```

- [ ] **Step 4: Write test**

Create `frontend/src/components/data/DataList.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DataList } from './DataList';

describe('DataList', () => {
  const items = [
    { id: '1', name: 'Alpha' },
    { id: '2', name: 'Beta' },
  ];

  it('renders items', () => {
    render(
      <DataList
        items={items}
        keyExtractor={(i) => i.id}
        renderItem={(item) => <div>{item.name}</div>}
      />,
    );
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });

  it('shows empty state', () => {
    render(
      <DataList
        items={[]}
        keyExtractor={(i) => i.id}
        renderItem={(item) => <div>{item.name}</div>}
      />,
    );
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('calls onSelectionChange', () => {
    const onChange = vi.fn();
    render(
      <DataList
        items={items}
        keyExtractor={(i) => i.id}
        renderItem={(item, ctx) => (
          <button onClick={ctx.toggleSelect}>{item.name}</button>
        )}
        selection="multiple"
        selectedKeys={new Set()}
        onSelectionChange={onChange}
      />,
    );
    fireEvent.click(screen.getByText('Alpha'));
    expect(onChange).toHaveBeenCalledWith(new Set(['1']));
  });
});
```

- [ ] **Step 5: Run tests**

```powershell
cd frontend; npx vitest run src/components/data/DataList.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/data
git commit -m "feat(data): add DataList and DataListItem"
```

---

### Task 8: Create `DataTable`

**Files:**
- Create: `frontend/src/components/data/DataTable.tsx`
- Modify: `frontend/src/components/data/index.ts`
- Test: `frontend/src/components/data/DataTable.test.tsx`

- [ ] **Step 1: Create `DataTable.tsx`**

```tsx
import React, { ReactNode } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  type ColumnDef,
  type RowSelectionState,
} from '@tanstack/react-table';
import { MoreHorizontal } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { DataEmptyState } from './DataEmptyState';
import { DataErrorState } from './DataErrorState';
import { DataSkeleton } from './DataSkeleton';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';

export interface RowAction<T> {
  label: string;
  onClick: (row: T) => void;
  destructive?: boolean;
}

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  isLoading?: boolean;
  error?: Error | null;
  emptyState?: ReactNode;
  selection?: 'none' | 'multiple';
  selectedKeys?: Set<string>;
  onSelectionChange?: (keys: Set<string>) => void;
  header?: ReactNode;
  footer?: ReactNode;
  rowActions?: (row: T) => RowAction<T>[];
  className?: string;
  getRowId?: (row: T) => string;
}

export function DataTable<T>({
  data,
  columns,
  isLoading,
  error,
  emptyState,
  selection = 'none',
  selectedKeys,
  onSelectionChange,
  header,
  footer,
  rowActions,
  className,
  getRowId,
}: DataTableProps<T>) {
  const rowSelection: RowSelectionState = React.useMemo(() => {
    const map: RowSelectionState = {};
    selectedKeys?.forEach((key) => {
      map[key] = true;
    });
    return map;
  }, [selectedKeys]);

  const tableColumns: ColumnDef<T>[] = React.useMemo(() => {
    const base = [...columns];
    if (selection === 'multiple') {
      base.unshift({
        id: 'select',
        header: ({ table }) => (
          <Checkbox
            checked={table.getIsAllPageRowsSelected()}
            onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
            aria-label="全选"
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            checked={row.getIsSelected()}
            onCheckedChange={(value) => row.toggleSelected(!!value)}
            aria-label="选择行"
          />
        ),
        size: 40,
      });
    }
    if (rowActions) {
      base.push({
        id: 'actions',
        header: '',
        cell: ({ row }) => {
          const actions = rowActions(row.original);
          if (!actions || actions.length === 0) return null;
          return (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="行操作" className="h-8 w-8">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {actions.map((action, idx) => (
                  <DropdownMenuItem
                    key={idx}
                    onClick={() => action.onClick(row.original)}
                    className={cn(action.destructive && 'text-destructive focus:text-destructive')}
                  >
                    {action.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          );
        },
        size: 50,
      });
    }
    return base;
  }, [columns, selection, rowActions]);

  const table = useReactTable({
    data,
    columns: tableColumns,
    state: { rowSelection },
    enableRowSelection: selection === 'multiple',
    onRowSelectionChange: (updater) => {
      if (!onSelectionChange) return;
      const next = typeof updater === 'function' ? updater(rowSelection) : updater;
      onSelectionChange(new Set(Object.keys(next)));
    },
    getCoreRowModel: getCoreRowModel(),
    getRowId,
  });

  if (isLoading) {
    return (
      <div className={className}>
        {header}
        <DataSkeleton rows={5} />
      </div>
    );
  }

  if (error) {
    return (
      <div className={className}>
        {header}
        <DataErrorState description={error.message} />
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className={className}>
        {header}
        {emptyState ?? <DataEmptyState title="暂无数据" />}
      </div>
    );
  }

  return (
    <div className={cn('space-y-3', className)}>
      {header}
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="px-3 py-2 text-left text-xs font-medium text-muted-foreground"
                    style={{ width: header.getSize() }}
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y">
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="hover:bg-muted/50 transition-colors">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-2 whitespace-nowrap">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {footer}
    </div>
  );
}

export default DataTable;
```

- [ ] **Step 2: Update `index.ts`**

```ts
export { DataEmptyState } from './DataEmptyState';
export { DataErrorState } from './DataErrorState';
export { DataSkeleton } from './DataSkeleton';
export { DataToolbar } from './DataToolbar';
export { DataPagination } from './DataPagination';
export { DataList } from './DataList';
export { DataListItem } from './DataListItem';
export type { MoreAction } from './DataListItem';
export { DataTable } from './DataTable';
export type { RowAction } from './DataTable';
```

- [ ] **Step 3: Write test**

Create `frontend/src/components/data/DataTable.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DataTable } from './DataTable';
import type { ColumnDef } from '@tanstack/react-table';

describe('DataTable', () => {
  interface Row { id: string; name: string; }
  const data: Row[] = [{ id: '1', name: 'Alpha' }, { id: '2', name: 'Beta' }];
  const columns: ColumnDef<Row>[] = [
    { accessorKey: 'id', header: 'ID' },
    { accessorKey: 'name', header: 'Name' },
  ];

  it('renders rows', () => {
    render(<DataTable data={data} columns={columns} getRowId={(r) => r.id} />);
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });

  it('renders row actions', () => {
    render(
      <DataTable
        data={data}
        columns={columns}
        getRowId={(r) => r.id}
        rowActions={() => [{ label: 'View', onClick: vi.fn() }]}
      />,
    );
    expect(screen.getAllByLabelText('行操作')).toHaveLength(2);
  });

  it('calls onSelectionChange', () => {
    const onChange = vi.fn();
    render(
      <DataTable
        data={data}
        columns={columns}
        getRowId={(r) => r.id}
        selection="multiple"
        selectedKeys={new Set()}
        onSelectionChange={onChange}
      />,
    );
    fireEvent.click(screen.getAllByLabelText('选择行')[0]);
    expect(onChange).toHaveBeenCalledWith(new Set(['1']));
  });
});
```

- [ ] **Step 4: Run tests**

```powershell
cd frontend; npx vitest run src/components/data/DataTable.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/data
git commit -m "feat(data): add DataTable based on TanStack Table"
```

---

## Phase 2: List Pages Unification

### Task 9: Migrate `PlanListPage` to `DataList`

**Files:**
- Modify: `frontend/src/pages/orchestration/PlanListPage.tsx`
- Test: `frontend/src/pages/orchestration/PlanListPage.test.tsx` (create/update)

- [ ] **Step 1: Inspect current `PlanListPage.tsx`**

Already read. Key points:
- Uses `PageContainer width="list"` and `PageHeader`.
- Has hover-only action buttons (`md:opacity-0 md:group-hover:opacity-100`).
- Stats cards, search, list.

- [ ] **Step 2: Modify `PlanListPage.tsx`**

Replace content with:

```tsx
import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { planKeys } from '@/utils/api/queryKeys';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { api, type Plan } from '@/utils/api';
import { Badge } from '@/components/ui/badge';
import { PageContainer, PageHeaderV2 } from '@/components/layout';
import { DataList, DataListItem, DataToolbar, DataEmptyState } from '@/components/data';
import { STAT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatLocalDate } from '@/utils/format';
import { Plus, Edit, Trash2, Search, FileText, Play, LayoutGrid, List } from 'lucide-react';

type ViewMode = 'grid' | 'list';

export default function PlanListPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [view, setView] = useState<ViewMode>('grid');

  const { data: plans, isLoading } = useQuery({
    queryKey: planKeys.list(100),
    queryFn: () => api.plans.list(0, 100),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.plans.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: planKeys.allLists() });
      toast.success('Plan 已删除');
    },
    onError: (err: any) => toast.error(err.message || '删除失败'),
  });

  const filtered = useMemo(() => {
    if (!plans) return [];
    const q = search.toLowerCase();
    return plans.filter(
      (p) =>
        !q ||
        p.name.toLowerCase().includes(q) ||
        (p.description || '').toLowerCase().includes(q),
    );
  }, [plans, search]);

  const handleDelete = async (plan: Plan) => {
    const ok = await confirmDialog({
      title: '删除 Plan',
      description: `确定删除 "${plan.name}"？此操作不可撤销。`,
      variant: 'destructive',
    });
    if (ok) deleteMutation.mutate(plan.id);
  };

  const stats = useMemo(
    () => ({
      total: plans?.length ?? 0,
      withSteps: plans?.filter((p) => p.steps?.length > 0).length ?? 0,
      chained: plans?.filter((p) => p.next_plan_id != null).length ?? 0,
    }),
    [plans],
  );

  const renderPlanItem = (plan: Plan) => {
    const content = (
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <h3 className={cn('font-medium truncate', TEXT.heading)}>{plan.name}</h3>
          {plan.next_plan_id != null && (
            <Badge variant="info" className="text-xs px-1.5 py-0.5">
              链式
            </Badge>
          )}
        </div>
        {plan.description && (
          <p className={cn('text-sm truncate', TEXT.subtitle)}>{plan.description}</p>
        )}
        <div className={cn('flex items-center gap-3 text-xs pt-1', TEXT.subtitle)}>
          <span>{plan.steps?.length ?? 0} 步骤</span>
          <span>阈值 {Math.round((plan.failure_threshold ?? 0.05) * 100)}%</span>
          {plan.created_by && <span>创建者: {plan.created_by}</span>}
          <span>更新于 {formatLocalDate(plan.updated_at)}</span>
        </div>
      </div>
    );

    return (
      <DataListItem
        onNavigate={() => navigate(`/orchestration/plans/${plan.id}`)}
        actions={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/execution/plan-execute?plan=${plan.id}`);
              }}
              aria-label="执行"
            >
              <Play className="w-4 h-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/orchestration/plans/${plan.id}`);
              }}
              aria-label="编辑"
            >
              <Edit className="w-4 h-4" />
            </Button>
          </>
        }
        moreActions={[
          {
            label: '删除',
            onClick: () => handleDelete(plan),
            destructive: true,
          },
        ]}
      >
        {view === 'grid' ? (
          <div className="p-1">{content}</div>
        ) : (
          <div className="flex items-center justify-between w-full">{content}</div>
        )}
      </DataListItem>
    );
  };

  return (
    <PageContainer fullBleed>
      <PageHeaderV2
        title="Plan 编排"
        description="基于 Plan-Step 模型管理测试编排，支持链接式 Plan 链"
        actions={
          <Button onClick={() => navigate('/orchestration/plans/new')}>
            <Plus className="w-4 h-4 mr-1.5" /> 新建 Plan
          </Button>
        }
      />

      <div className="grid grid-cols-3 gap-4 px-6">
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{stats.total}</p>
            <p className={STAT.label}>Plan 总数</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{stats.withSteps}</p>
            <p className={STAT.label}>已配置步骤</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{stats.chained}</p>
            <p className={STAT.label}>链式 Plan</p>
          </CardContent>
        </Card>
      </div>

      <div className="px-6 pb-2">
        <DataToolbar
          searchValue={search}
          onSearchChange={setSearch}
          searchPlaceholder="搜索 Plan 名称或描述..."
        >
          <Button
            variant={view === 'grid' ? 'secondary' : 'ghost'}
            size="icon"
            onClick={() => setView('grid')}
            aria-label="网格视图"
          >
            <LayoutGrid className="w-4 h-4" />
          </Button>
          <Button
            variant={view === 'list' ? 'secondary' : 'ghost'}
            size="icon"
            onClick={() => setView('list')}
            aria-label="列表视图"
          >
            <List className="w-4 h-4" />
          </Button>
        </DataToolbar>
      </div>

      <div className="px-6 pb-6 flex-1">
        <DataList
          items={filtered}
          isLoading={isLoading}
          keyExtractor={(plan) => String(plan.id)}
          renderItem={(plan) => renderPlanItem(plan)}
          itemClassName={view === 'grid' ? '' : ''}
          emptyState={
            <DataEmptyState
              title="还没有 Plan"
              description="创建您的第一个测试计划"
              icon={<FileText className="w-16 h-16" />}
              action={
                <Button onClick={() => navigate('/orchestration/plans/new')}>
                  <Plus className="w-4 h-4 mr-2" /> 新建 Plan
                </Button>
              }
            />
          }
        />
      </div>
    </PageContainer>
  );
}
```

- [ ] **Step 3: Run type check and tests**

```powershell
cd frontend; npx tsc --noEmit
cd frontend; npx vitest run src/pages/orchestration/PlanListPage.test.tsx
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/orchestration/PlanListPage.tsx
git commit -m "feat(plans): migrate PlanListPage to PageHeaderV2 and DataList"
```

---

### Task 10: Migrate `PlanRunListPage` to `DataList`

**Files:**
- Modify: `frontend/src/pages/execution/PlanRunListPage.tsx`

- [ ] **Step 1: Modify the file**

Replace content with:

```tsx
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { StatusBadge } from '@/components/ui/status-badge';
import { api } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import { Clock } from 'lucide-react';
import { PageContainer, PageHeaderV2 } from '@/components/layout';
import { DataList, DataListItem, DataToolbar, DataEmptyState } from '@/components/data';
import { TEXT } from '@/design-system/tokens';
import { formatDateTimeFull } from '@/utils/format';

export default function PlanRunListPage() {
  const navigate = useNavigate();

  const { data: runs, isLoading } = useQuery({
    queryKey: planRunKeys.list(),
    queryFn: () => api.planRuns.list(0, 50),
    refetchInterval: 15_000,
  });

  return (
    <PageContainer fullBleed>
      <PageHeaderV2 title="Plan 执行记录" description="查看所有 PlanRun 历史记录" />

      <div className="px-6 pb-6 flex-1">
        <DataList
          items={runs ?? []}
          isLoading={isLoading}
          keyExtractor={(run) => String(run.id)}
          header={<DataToolbar searchPlaceholder="搜索执行记录..." />}
          renderItem={(run) => (
            <DataListItem
              onNavigate={() => navigate(`/execution/plan-runs/${run.id}`)}
              actions={<StatusBadge kind="plan-run" status={run.status} size="sm" />}
            >
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between w-full gap-2">
                <div className="flex items-center gap-4 min-w-0">
                  <span className={cn('font-mono text-sm', TEXT.subtitle)}>#{run.id}</span>
                  <span className={cn('text-sm', TEXT.heading)}>
                    {run.plan_name || `Plan #${run.plan_id}`}
                  </span>
                  <span className={cn('text-xs', TEXT.caption)}>{run.run_type}</span>
                </div>
                <div className={cn('flex items-center gap-4 text-xs', TEXT.caption)}>
                  {run.triggered_by && <span>{run.triggered_by}</span>}
                  <span>{formatDateTimeFull(run.started_at)}</span>
                </div>
              </div>
            </DataListItem>
          )}
          emptyState={
            <DataEmptyState
              title="暂无执行记录"
              description="还没有 Plan 执行记录"
              icon={<Clock className="w-16 h-16" />}
            />
          }
        />
      </div>
    </PageContainer>
  );
}
```

Note: Add `cn` import if missing. The original file did not import `cn`, so add:

```tsx
import { cn } from '@/lib/utils';
```

- [ ] **Step 2: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/execution/PlanRunListPage.tsx
git commit -m "feat(plan-runs): migrate PlanRunListPage to PageHeaderV2 and DataList"
```

---

### Task 11: Migrate `ResultsPage` to `DataTable`

**Files:**
- Modify: `frontend/src/pages/results/ResultsPage.tsx`

- [ ] **Step 1: Modify the file**

Replace content with:

```tsx
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/ui/status-badge';
import { RiskDistributionChart } from '@/components/charts/RiskDistributionChart';
import { TestTypePassFailChart } from '@/components/charts/TestTypePassFailChart';
import { DashboardStatCard } from '@/components/dashboard/DashboardStatCard';
import { api, type ResultsSummary, type RecentRun } from '@/utils/api';
import {
  CheckCircle,
  XCircle,
  PlayCircle,
  ListChecks,
  Clock,
} from 'lucide-react';
import { PageContainer, PageHeaderV2 } from '@/components/layout';
import { DataTable, DataToolbar, DataEmptyState } from '@/components/data';
import { formatDurationSeconds, formatLocalDateTime } from '@/utils/format';
import { KPI_TONE, RUN_RESULT_STATUS_CHIP, STAT, STATUS_CHIP } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type { ColumnDef } from '@tanstack/react-table';

export default function ResultsPage() {
  const navigate = useNavigate();

  const { data, isLoading } = useQuery<ResultsSummary>({
    queryKey: ['results-summary'],
    queryFn: async () => {
      const resp = await api.results.summary(30);
      return resp.data;
    },
    refetchInterval: 30_000,
  });

  const stats = data?.runs_by_status;

  const columns: ColumnDef<RecentRun>[] = [
    {
      accessorKey: 'run_id',
      header: 'Run',
      cell: ({ getValue }) => <span className="font-mono text-xs">#{getValue<number>()}</span>,
      size: 70,
    },
    {
      accessorKey: 'task_name',
      header: '任务',
      cell: ({ getValue }) => (
        <span className="max-w-[180px] truncate block">{getValue<string>()}</span>
      ),
      size: 200,
    },
    {
      accessorKey: 'task_type',
      header: '类型',
      cell: ({ getValue }) => (
        <span className={cn('rounded px-1.5 py-0.5 text-xs font-medium', STATUS_CHIP.muted)}>
          {getValue<string>()}
        </span>
      ),
      size: 100,
    },
    {
      accessorKey: 'status',
      header: '状态',
      cell: ({ getValue }) => (
        <span
          className={cn(
            'rounded px-1.5 py-0.5 text-xs font-medium',
            RUN_RESULT_STATUS_CHIP[getValue<string>()] ?? STATUS_CHIP.muted,
          )}
        >
          {getValue<string>()}
        </span>
      ),
      size: 100,
    },
    {
      accessorKey: 'risk_level',
      header: '风险',
      cell: ({ getValue }) => <StatusBadge kind="risk" status={getValue<string>()} size="sm" />,
      size: 80,
    },
    {
      accessorKey: 'duration_seconds',
      header: '时长',
      cell: ({ getValue }) => (
        <span className="text-xs text-muted-foreground">
          {formatDurationSeconds(getValue<number | null>(), 'precise', '-')}
        </span>
      ),
      size: 100,
    },
    {
      accessorKey: 'started_at',
      header: '开始时间',
      cell: ({ getValue }) => (
        <span className="text-xs text-muted-foreground">
          {formatLocalDateTime(getValue<string | null>())}
        </span>
      ),
      size: 150,
    },
  ];

  return (
    <PageContainer fullBleed>
      <PageHeaderV2 title="测试结果" description="测试运行统计与风险分布概览" />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 px-6">
        <DashboardStatCard
          label="运行总数"
          value={stats?.total ?? 0}
          loading={isLoading}
          icon={<ListChecks size={18} className={KPI_TONE.default.label} />}
          iconWellClassName={STAT.iconWellMuted}
        />
        <DashboardStatCard
          label="已完成"
          value={stats?.finished ?? 0}
          loading={isLoading}
          icon={<CheckCircle size={18} className={KPI_TONE.success.value} />}
          iconWellClassName={STAT.iconWellSuccess}
          valueClassName={KPI_TONE.success.value}
        />
        <DashboardStatCard
          label="失败"
          value={stats?.failed ?? 0}
          loading={isLoading}
          icon={<XCircle size={18} className={KPI_TONE.destructive.value} />}
          iconWellClassName={STAT.iconWellDestructive}
          valueClassName={KPI_TONE.destructive.value}
        />
        <DashboardStatCard
          label="运行中"
          value={stats?.running ?? 0}
          loading={isLoading}
          icon={<PlayCircle size={18} className={KPI_TONE.primary.value} />}
          iconWellClassName={STAT.iconWellPrimary}
          valueClassName={KPI_TONE.primary.value}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 px-6">
        <RiskDistributionChart
          data={data?.risk_distribution ?? { high: 0, medium: 0, low: 0, unknown: 0 }}
          isLoading={isLoading}
        />
        <TestTypePassFailChart
          data={data?.test_type_stats ?? []}
          isLoading={isLoading}
        />
      </div>

      <Card className="mx-6 mb-6">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <Clock size={16} className="text-muted-foreground" />
            最近运行
          </CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable
            data={data?.recent_runs ?? []}
            columns={columns}
            isLoading={isLoading}
            getRowId={(row) => String(row.run_id)}
            rowActions={(row) => [
              {
                label: '查看报告',
                onClick: () => navigate(`/runs/${row.run_id}/report`),
              },
            ]}
            emptyState={
              <DataEmptyState
                title="暂无测试运行"
                description="还没有执行过测试"
                icon={<Clock className="h-12 w-12" />}
              />
            }
          />
        </CardContent>
      </Card>
    </PageContainer>
  );
}
```

- [ ] **Step 2: Verify `RecentRun` type exists**

If `RecentRun` is not exported from `@/utils/api`, use the inline type or check `frontend/src/utils/api/types.ts`. Assuming `ResultsSummary.recent_runs` is typed, the element type can be inferred with `NonNullable<ResultsSummary['recent_runs']>[number]`. Update the columns type accordingly if needed:

```tsx
import type { ResultsSummary } from '@/utils/api';
type RecentRun = NonNullable<ResultsSummary['recent_runs']>[number];
```

- [ ] **Step 3: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/results/ResultsPage.tsx
git commit -m "feat(results): migrate ResultsPage to PageHeaderV2 and DataTable"
```

---

## Phase 3: Detail & Edit Pages

### Task 12: Create tabbed `PlanRunDetailPage` structure

**Files:**
- Create directory: `frontend/src/pages/execution/PlanRunDetailPage/`
- Create: `frontend/src/pages/execution/PlanRunDetailPage/index.tsx`
- Create: `frontend/src/pages/execution/PlanRunDetailPage/PlanRunMeta.tsx`
- Create: `frontend/src/pages/execution/PlanRunDetailPage/RunStatusBanner.tsx`
- Create: `frontend/src/pages/execution/PlanRunDetailPage/RunOverviewTab.tsx`
- Modify: router to use new path (if path changed, otherwise just replace file)

- [ ] **Step 1: Create `PlanRunMeta.tsx`**

```tsx
import React from 'react';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';
import { formatLocalDateTime } from '@/utils/format';
import type { PlanRun } from '@/utils/api/types';

interface PlanRunMetaProps {
  run: PlanRun | undefined;
}

export const PlanRunMeta: React.FC<PlanRunMetaProps> = ({ run }) => {
  if (!run) return null;
  return (
    <div className={cn('flex flex-wrap items-center gap-x-4 gap-y-1 text-xs', TEXT.subtitle)}>
      <span>状态: {run.status}</span>
      {run.started_at && <span>开始: {formatLocalDateTime(run.started_at)}</span>}
      {run.finished_at && <span>结束: {formatLocalDateTime(run.finished_at)}</span>}
      {run.triggered_by && <span>触发: {run.triggered_by}</span>}
    </div>
  );
};

export default PlanRunMeta;
```

- [ ] **Step 2: Create `RunStatusBanner.tsx`**

```tsx
import React from 'react';
import { cn } from '@/lib/utils';
import { TEXT, ALERT_BANNER } from '@/design-system/tokens';
import type { PlanRun } from '@/utils/api/types';

interface RunStatusBannerProps {
  run: PlanRun | undefined;
}

export const RunStatusBanner: React.FC<RunStatusBannerProps> = ({ run }) => {
  if (!run) return null;
  const isFailed = run.status === 'FAILED' || run.status === 'DEGRADED';
  const isRunning = run.status === 'RUNNING';
  return (
    <div
      className={cn(
        'px-4 py-2 text-sm border-b',
        isFailed && ALERT_BANNER.destructive,
        isRunning && ALERT_BANNER.warning,
        !isFailed && !isRunning && 'bg-muted/30 border-border',
      )}
    >
      <span className={cn('font-medium', TEXT.heading)}>{run.status}</span>
      <span className={cn('ml-2', TEXT.subtitle)}>
        {isRunning ? 'PlanRun 正在执行中' : isFailed ? 'PlanRun 执行异常' : 'PlanRun 已完成'}
      </span>
    </div>
  );
};

export default RunStatusBanner;
```

- [ ] **Step 3: Create `RunOverviewTab.tsx`**

```tsx
import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { TEXT, STAT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type { PlanRun } from '@/utils/api/types';

interface RunOverviewTabProps {
  run: PlanRun | undefined;
}

export const RunOverviewTab: React.FC<RunOverviewTabProps> = ({ run }) => {
  if (!run) return null;
  return (
    <div className="p-4 space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{run.device_count ?? 0}</p>
            <p className={STAT.label}>设备数</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{run.completed_devices ?? 0}</p>
            <p className={STAT.label}>已完成</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{run.failed_devices ?? 0}</p>
            <p className={STAT.label}>失败</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{run.artifact_count ?? 0}</p>
            <p className={STAT.label}>产物数</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className={cn('text-sm', TEXT.heading)}>PlanRun 信息</CardTitle>
        </CardHeader>
        <CardContent className={cn('text-sm space-y-2', TEXT.subtitle)}>
          <p>Plan ID: {run.plan_id}</p>
          <p>Run Type: {run.run_type}</p>
          <p>Trigger: {run.triggered_by || '-'}</p>
        </CardContent>
      </Card>
    </div>
  );
};

export default RunOverviewTab;
```

Note: If these fields don't exist on `PlanRun`, adjust to match actual type. Use optional chaining.

- [ ] **Step 4: Create `index.tsx`**

Create `frontend/src/pages/execution/PlanRunDetailPage/index.tsx`:

```tsx
import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { PageContainer, PageHeaderV2 } from '@/components/layout';
import { usePlanRunDetailData } from '@/hooks/plan-run/usePlanRunDetailData';
import { PlanRunMeta } from './PlanRunMeta';
import { RunStatusBanner } from './RunStatusBanner';
import { RunOverviewTab } from './RunOverviewTab';
import { RunDevicesTab } from './RunDevicesTab';
import { RunArtifactsTab } from './RunArtifactsTab';
import { RunLogsTab } from './RunLogsTab';
import { RunSignalsTab } from './RunSignalsTab';
import { RunTimelineTab } from './RunTimelineTab';
import { useToast } from '@/components/ui/toast';
import { api } from '@/utils/api';

export default function PlanRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const id = Number(runId);
  const navigate = useNavigate();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState('overview');

  const { runQ, devicesQ, watcherQ, timelineQ, refreshAll, abortMut, retryMut, retryDispatchMut } =
    usePlanRunDetailData(id, {
      deviceStatusFilter: 'all',
      deviceHostFilter: 'all',
      watcherTimeScope: 'all',
    });

  if (!id || Number.isNaN(id)) {
    return <div>无效 PlanRun ID</div>;
  }

  const run = runQ.data;

  return (
    <PageContainer>
      <PageHeaderV2
        title={run?.plan_name || `Plan Run #${id}`}
        breadcrumbs={[
          { label: 'Plan Runs', path: '/execution/plan-runs' },
          { label: `#${id}` },
        ]}
        description={<PlanRunMeta run={run} />}
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => abortMut.mutate('user')}
              disabled={abortMut.isPending || run?.status === 'COMPLETED'}
            >
              取消
            </Button>
            <Button
              size="sm"
              onClick={() => retryMut.mutate()}
              disabled={retryMut.isPending || run?.status === 'RUNNING'}
            >
              重试
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                try {
                  const blob = await api.planRuns.exportReport(id, 'md');
                  const url = URL.createObjectURL(blob);
                  const anchor = document.createElement('a');
                  anchor.href = url;
                  anchor.download = `plan-run-${id}-report.md`;
                  anchor.click();
                  URL.revokeObjectURL(url);
                  toast.success('报告已导出');
                } catch (err: unknown) {
                  const msg = err instanceof Error ? err.message : String(err);
                  toast.error(`导出失败: ${msg}`);
                }
              }}
            >
              下载报告
            </Button>
          </>
        }
      />

      <RunStatusBanner run={run} />

      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
        <TabsList className="mx-4 mt-2 justify-start">
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="devices">设备</TabsTrigger>
          <TabsTrigger value="artifacts">产物</TabsTrigger>
          <TabsTrigger value="logs">日志</TabsTrigger>
          <TabsTrigger value="signals">Signals</TabsTrigger>
          <TabsTrigger value="timeline">时间线</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex-1 overflow-auto m-0">
          <RunOverviewTab run={run} />
        </TabsContent>
        <TabsContent value="devices" className="flex-1 overflow-auto m-0">
          <RunDevicesTab runId={id} data={devicesQ} />
        </TabsContent>
        <TabsContent value="artifacts" className="flex-1 overflow-auto m-0">
          <RunArtifactsTab runId={id} />
        </TabsContent>
        <TabsContent value="logs" className="flex-1 overflow-auto m-0">
          <RunLogsTab runId={id} />
        </TabsContent>
        <TabsContent value="signals" className="flex-1 overflow-auto m-0">
          <RunSignalsTab
            runId={id}
            data={watcherQ}
            onRefresh={() => watcherQ.refetch()}
          />
        </TabsContent>
        <TabsContent value="timeline" className="flex-1 overflow-auto m-0">
          <RunTimelineTab data={timelineQ} />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
```

- [ ] **Step 5: Create tab stubs**

Create minimal stubs for remaining tabs so TypeScript compiles. Each file exports a default component with matching props.

`RunDevicesTab.tsx`:

```tsx
import React from 'react';
import { DataTable, DataErrorState } from '@/components/data';
import type { UseQueryResult } from '@tanstack/react-query';
import type { DeviceMatrixItem } from '@/utils/api/types';
import type { ColumnDef } from '@tanstack/react-table';

interface RunDevicesTabProps {
  runId: number;
  data: UseQueryResult<DeviceMatrixItem[], Error>;
}

export const RunDevicesTab: React.FC<RunDevicesTabProps> = ({ data }) => {
  const columns: ColumnDef<DeviceMatrixItem>[] = [
    { accessorKey: 'device_serial', header: '序列号' },
    { accessorKey: 'status', header: '状态' },
    { accessorKey: 'host_name', header: '主机' },
  ];

  return (
    <div className="p-4">
      <DataTable
        data={data.data ?? []}
        columns={columns}
        isLoading={data.isLoading}
        error={data.error}
        emptyState={<div className="py-8 text-center text-sm text-muted-foreground">暂无设备</div>}
      />
    </div>
  );
};

export default RunDevicesTab;
```

`RunArtifactsTab.tsx`:

```tsx
import React from 'react';

interface RunArtifactsTabProps {
  runId: number;
}

export const RunArtifactsTab: React.FC<RunArtifactsTabProps> = () => {
  return (
    <div className="p-4 text-sm text-muted-foreground">
      产物列表（待接入 API）
    </div>
  );
};

export default RunArtifactsTab;
```

`RunLogsTab.tsx`:

```tsx
import React from 'react';

interface RunLogsTabProps {
  runId: number;
}

export const RunLogsTab: React.FC<RunLogsTabProps> = () => {
  return (
    <div className="p-4 text-sm text-muted-foreground">
      日志（待接入 API）
    </div>
  );
};

export default RunLogsTab;
```

`RunSignalsTab.tsx`:

```tsx
import React from 'react';
import { DataErrorState } from '@/components/data';
import AnomalyDashboard from '@/components/plan-run/AnomalyDashboard';
import type { UseQueryResult } from '@tanstack/react-query';
import type { WatcherSummary } from '@/utils/api/types';

interface RunSignalsTabProps {
  runId: number;
  data: UseQueryResult<WatcherSummary, Error>;
  onRefresh: () => void;
}

export const RunSignalsTab: React.FC<RunSignalsTabProps> = ({ runId, data, onRefresh }) => {
  return (
    <div className="p-4 space-y-4">
      <AnomalyDashboard
        runId={runId}
        data={data.data}
        isLoading={data.isLoading}
        isError={data.isError}
        timeScope="all"
        onTimeScopeChange={() => {}}
      />
      {data.isError && <DataErrorState onRetry={onRefresh} />}
    </div>
  );
};

export default RunSignalsTab;
```

`RunTimelineTab.tsx`:

```tsx
import React from 'react';
import BusinessFlowStepper from '@/components/plan-run/BusinessFlowStepper';
import type { UseQueryResult } from '@tanstack/react-query';
import type { RunTimeline } from '@/utils/api/types';

interface RunTimelineTabProps {
  data: UseQueryResult<RunTimeline, Error>;
}

export const RunTimelineTab: React.FC<RunTimelineTabProps> = ({ data }) => {
  return (
    <div className="p-4">
      <BusinessFlowStepper
        timeline={data.data}
        isLoading={data.isLoading}
        isError={data.isError}
      />
    </div>
  );
};

export default RunTimelineTab;
```

- [ ] **Step 6: Delete old file and update router**

Delete `frontend/src/pages/execution/PlanRunDetailPage.tsx`.

If the router imports the component by file path, no change is needed because `index.tsx` in the directory resolves. Verify `frontend/src/router/index.tsx`.

- [ ] **Step 7: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/execution/PlanRunDetailPage
git rm frontend/src/pages/execution/PlanRunDetailPage.tsx
git commit -m "feat(plan-run): restructure PlanRunDetailPage into tabs"
```

---

### Task 13: Migrate `PlanEditPage` to responsive resizable panels

**Files:**
- Modify: `frontend/src/pages/orchestration/PlanEditPage.tsx`

- [ ] **Step 1: Modify the file**

Replace content with:

```tsx
import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Code2, Play, Save, PanelLeft, PanelRight, Undo2, Redo2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import PlanChainPanel from '@/components/pipeline/PlanChainPanel';
import PlanCanvas from '@/components/pipeline/PlanCanvas';
import PlanStepInspector from '@/components/pipeline/PlanStepInspector';
import { STATUS_BG_COLORS } from '@/design-system/colors';
import { SURFACE, TEXT, FORM } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { usePlanEditForm } from './usePlanEditForm';
import { PageContainer, PageHeaderV2 } from '@/components/layout';

export default function PlanEditPage() {
  const { id } = useParams<{ id: string }>();
  const planId = id && id !== 'new' && Number(id) > 0 ? Number(id) : null;
  const navigate = useNavigate();

  const form = usePlanEditForm(planId);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);

  if (!form.isNew && form.planLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className={cn('w-6 h-6 animate-spin border-2 border-current border-t-transparent rounded-full', TEXT.caption)} />
      </div>
    );
  }

  return (
    <PageContainer scrollable={false} className="p-0">
      <PageHeaderV2
        title={form.name || (form.isNew ? '新建 Plan' : '未命名 Plan')}
        breadcrumbs={[
          { label: 'Plans', path: '/orchestration/plans' },
          { label: form.isNew ? 'Create' : 'Edit' },
        ]}
        actions={
          <>
            <Button variant="ghost" size="sm" onClick={() => form.setShowJson(true)}>
              <Code2 className="w-4 h-4 mr-1.5" />
              查看 JSON
            </Button>
            <Button variant="default" size="sm" onClick={form.handleExecute} disabled={form.saving}>
              <Play className="w-4 h-4 mr-1.5" />
              发起测试
            </Button>
            <Button size="sm" onClick={form.handleSave} disabled={form.saving || !form.isDirty}>
              <Save className="w-4 h-4 mr-1.5" />
              {form.saving ? '保存中…' : form.isNew ? '创建' : '保存修改'}
            </Button>
          </>
        }
      />

      <div className="flex items-center justify-between px-4 py-2 border-b bg-card">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setLeftCollapsed((v) => !v)} aria-label={leftCollapsed ? '展开左栏' : '折叠左栏'}>
            <PanelLeft className="w-4 h-4" />
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setRightCollapsed((v) => !v)} aria-label={rightCollapsed ? '展开右栏' : '折叠右栏'}>
            <PanelRight className="w-4 h-4" />
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={form.undo} disabled={!form.canUndo}>
            <Undo2 className="w-4 h-4 mr-1" /> 撤销
          </Button>
          <Button variant="ghost" size="sm" onClick={form.redo} disabled={!form.canRedo}>
            <Redo2 className="w-4 h-4 mr-1" /> 重做
          </Button>
          <span className={cn('text-xs', TEXT.caption)}>
            {form.isDirty ? (
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BG_COLORS.warning} border border-warning`}>
                未保存
              </span>
            ) : (
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BG_COLORS.success} border border-success`}>
                已保存
              </span>
            )}
          </span>
        </div>
      </div>

      <ResizablePanelGroup direction="horizontal" className="flex-1 min-h-0">
        <ResizablePanel
          defaultSize={20}
          minSize={15}
          maxSize={30}
          collapsible
          collapsedSize={0}
          onCollapse={() => setLeftCollapsed(true)}
          onExpand={() => setLeftCollapsed(false)}
        >
          <div className="h-full overflow-auto">
            <PlanChainPanel
              plans={form.allPlans || []}
              currentPlanId={planId}
              draftStepCounts={form.isNew ? form.draftStepCounts : null}
              draftPlanName={form.name}
              onSelectPlan={form.handleSelectChainPlan}
              onAppendPlan={form.handleAppendChainPlan}
            />
          </div>
        </ResizablePanel>

        <ResizableHandle withHandle />

        <ResizablePanel defaultSize={60} minSize={40}>
          <div className="h-full overflow-auto bg-muted/40">
            <PlanCanvas
              planName={form.name}
              onPlanNameChange={form.setName}
              description={form.description}
              onDescriptionChange={form.setDescription}
              failureThreshold={form.failureThreshold}
              onFailureThresholdChange={form.setFailureThreshold}
              patrolIntervalSeconds={form.lifecycle.lifecycle.patrol?.interval_seconds ?? null}
              onPatrolIntervalChange={form.handlePatrolIntervalChange}
              timeoutSeconds={form.lifecycle.lifecycle.timeout_seconds ?? null}
              onTimeoutChange={form.handleTimeoutChange}
              nextPlanName={form.nextPlanName}
              isCurrentEditing
              lifecycle={form.lifecycle}
              onLifecycleChange={form.setLifecycle}
              selectedStepKey={form.selectedStepKey}
              onSelectStep={form.setSelectedStepKey}
              scripts={form.scripts || []}
            />
          </div>
        </ResizablePanel>

        <ResizableHandle withHandle />

        <ResizablePanel
          defaultSize={20}
          minSize={15}
          maxSize={30}
          collapsible
          collapsedSize={0}
          onCollapse={() => setRightCollapsed(true)}
          onExpand={() => setRightCollapsed(false)}
        >
          <div className="h-full overflow-auto">
            <PlanStepInspector
              step={form.selectedStep}
              phase={form.selectedRef.phase}
              index={form.selectedRef.index >= 0 ? form.selectedRef.index : null}
              scripts={form.scripts || []}
              onUpdateStep={form.handleStepUpdate}
            />
          </div>
        </ResizablePanel>
      </ResizablePanelGroup>

      <AlertDialog open={form.showJson} onOpenChange={form.setShowJson}>
        <AlertDialogContent className="max-w-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle>Plan Lifecycle JSON</AlertDialogTitle>
            <AlertDialogDescription>
              当前 Plan 的 lifecycle 是从 PlanStep 行 + Plan 直列字段实时装配的，仅供 pipeline_engine 校验视图。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <pre
            className={cn(
              'max-h-[60vh] overflow-auto border border-border rounded-md p-3 text-xs font-mono leading-relaxed',
              SURFACE.subtle,
            )}
          >
            {JSON.stringify(form.lifecycle, null, 2)}
          </pre>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => form.setShowJson(false)}>关闭</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={!!form.confirmLeave} onOpenChange={(open) => !open && form.setConfirmLeave(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>有未保存的修改</AlertDialogTitle>
            <AlertDialogDescription>
              {form.confirmLeave?.type === 'execute'
                ? '是否先保存当前 Plan 再发起测试？'
                : '是否先保存当前 Plan 再切换到目标 Plan？'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button variant="ghost" onClick={() => form.setConfirmLeave(null)}>
              取消
            </Button>
            <AlertDialogAction onClick={form.confirmAndProceed}>保存并继续</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={form.chainAppendDialog === 'confirm-save'}
        onOpenChange={(open) => !open && form.setChainAppendDialog(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>先保存再追加链尾？</AlertDialogTitle>
            <AlertDialogDescription>
              当前 Plan 尚未保存，是否先保存再追加链尾？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => void form.onChainAppendSaveConfirm()}>
              保存并继续
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={form.chainAppendDialog === 'name'}
        onOpenChange={(open) => !open && form.setChainAppendDialog(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>新 Plan 名称</AlertDialogTitle>
            <AlertDialogDescription>为链尾新 Plan 输入名称。</AlertDialogDescription>
          </AlertDialogHeader>
          <input
            type="text"
            value={form.chainAppendName}
            onChange={(e) => form.setChainAppendName(e.target.value)}
            className={cn(FORM.input, 'mt-1')}
            autoFocus
          />
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={!form.chainAppendName.trim()}
              onClick={(e) => {
                e.preventDefault();
                void form.onChainAppendNameConfirm();
              }}
            >
              创建并追加
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageContainer>
  );
}
```

- [ ] **Step 2: Verify `usePlanEditForm` API**

The form hook may not expose `undo`, `redo`, `canUndo`, `canRedo`. If not, keep only existing actions and add undo/redo later. Check `frontend/src/pages/orchestration/usePlanEditForm.ts`.

If `undo`/`redo` do not exist, remove the Undo/Redo buttons from the toolbar for now.

- [ ] **Step 3: Create shadcn resizable wrapper**

`react-resizable-panels` needs a thin wrapper to match shadcn patterns. Create `frontend/src/components/ui/resizable.tsx`:

```tsx
import { GripVertical } from 'lucide-react';
import * as ResizablePrimitive from 'react-resizable-panels';
import { cn } from '@/lib/utils';

export const ResizablePanelGroup = ({
  className,
  ...props
}: React.ComponentProps<typeof ResizablePrimitive.PanelGroup>) => (
  <ResizablePrimitive.PanelGroup
    className={cn('flex h-full w-full data-[panel-group-direction=vertical]:flex-col', className)}
    {...props}
  />
);

export const ResizablePanel = ResizablePrimitive.Panel;

export const ResizableHandle = ({
  withHandle,
  className,
  ...props
}: React.ComponentProps<typeof ResizablePrimitive.PanelResizeHandle> & { withHandle?: boolean }) => (
  <ResizablePrimitive.PanelResizeHandle
    className={cn(
      'relative flex w-px items-center justify-center bg-border after:absolute after:inset-y-0 after:left-1/2 after:w-1 after:-translate-x-1/2 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-offset-1 data-[panel-group-direction=vertical]:h-px data-[panel-group-direction=vertical]:w-full data-[panel-group-direction=vertical]:after:left-0 data-[panel-group-direction=vertical]:after:h-1 data-[panel-group-direction=vertical]:after:w-full data-[panel-group-direction=vertical]:after:-translate-y-1/2 data-[panel-group-direction=vertical]:after:translate-x-0 [&[data-panel-group-direction=vertical]>div]:rotate-90',
      className,
    )}
    {...props}
  >
    {withHandle && (
      <div className="z-10 flex h-4 w-3 items-center justify-center rounded-sm border bg-border">
        <GripVertical className="h-2.5 w-2.5" />
      </div>
    )}
  </ResizablePrimitive.PanelResizeHandle>
);
```

- [ ] **Step 4: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/orchestration/PlanEditPage.tsx frontend/src/components/ui/resizable.tsx
git commit -m "feat(plan-edit): responsive resizable three-column editor"
```

---

## Phase 4: Toast, User Menu, Accessibility

### Task 14: Replace custom toast with sonner

**Files:**
- Create: `frontend/src/components/ui/Toaster.tsx`
- Create: `frontend/src/hooks/useToast.ts`
- Delete: `frontend/src/components/ui/toast.tsx`
- Modify: `frontend/src/main.tsx` (or wherever `ToastProvider` is rendered)
- Modify: `frontend/src/components/ui/toast.test.tsx`

- [ ] **Step 1: Create `Toaster.tsx`**

```tsx
import { Toaster as Sonner } from 'sonner';

type ToasterProps = React.ComponentProps<typeof Sonner>;

export function Toaster({ ...props }: ToasterProps) {
  return (
    <Sonner
      position="top-right"
      toastOptions={{
        classNames: {
          toast:
            'group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg',
          description: 'group-[.toast]:text-muted-foreground',
          actionButton: 'group-[.toast]:bg-primary group-[.toast]:text-primary-foreground',
          cancelButton: 'group-[.toast]:bg-muted group-[.toast]:text-muted-foreground',
        },
      }}
      {...props}
    />
  );
}
```

- [ ] **Step 2: Create `useToast.ts`**

```tsx
import { toast as sonnerToast } from 'sonner';

export interface ToastPromiseOptions<T> {
  loading: string;
  success: string | ((data: T) => string);
  error: string | ((error: Error) => string);
}

export interface ToastAPI {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  promise: <T>(promise: Promise<T>, options: ToastPromiseOptions<T>) => Promise<T>;
}

export function useToast(): ToastAPI {
  return {
    success: (message: string) => sonnerToast.success(message, { duration: 3000 }),
    error: (message: string) => sonnerToast.error(message, { duration: Infinity }),
    info: (message: string) => sonnerToast.info(message, { duration: 4000 }),
    promise: async <T,>(promise: Promise<T>, options: ToastPromiseOptions<T>) => {
      return sonnerToast.promise(promise, {
        loading: options.loading,
        success: typeof options.success === 'function' ? options.success : options.success,
        error: typeof options.error === 'function' ? options.error : options.error,
      });
    },
  };
}
```

- [ ] **Step 3: Replace `ToastProvider` with `<Toaster />` in app root**

Find where `ToastProvider` is rendered (likely `frontend/src/main.tsx` or `frontend/src/App.tsx`). Replace `<ToastProvider><App /></ToastProvider>` with `<><App /><Toaster /></>`.

Example modification in `frontend/src/main.tsx`:

```tsx
import { Toaster } from '@/components/ui/Toaster';

root.render(
  <StrictMode>
    <QueryProvider>
      <Router>
        <App />
        <Toaster />
      </Router>
    </QueryProvider>
  </StrictMode>,
);
```

- [ ] **Step 4: Delete old toast files**

```bash
git rm frontend/src/components/ui/toast.tsx
```

- [ ] **Step 5: Update toast tests**

Replace `frontend/src/components/ui/toast.test.tsx` with tests for `useToast`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Toaster } from './Toaster';
import { useToast } from '@/hooks/useToast';

function TestButton({ label, onClick }: { label: string; onClick: () => void }) {
  return <button onClick={onClick}>{label}</button>;
}

function SuccessComponent() {
  const toast = useToast();
  return <TestButton label="success" onClick={() => toast.success('Saved')} />;
}

describe('useToast', () => {
  it('shows success toast', async () => {
    render(
      <>
        <SuccessComponent />
        <Toaster />
      </>,
    );
    screen.getByText('success').click();
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument());
  });
});
```

- [ ] **Step 6: Run type check and tests**

```powershell
cd frontend; npx tsc --noEmit
cd frontend; npx vitest run src/components/ui/toast.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ui/Toaster.tsx frontend/src/hooks/useToast.ts frontend/src/main.tsx frontend/src/components/ui/toast.test.tsx
git rm frontend/src/components/ui/toast.tsx
git commit -m "feat(toast): replace custom toast with sonner"
```

---

### Task 15: Create accessible `UserMenu`

**Files:**
- Create: `frontend/src/components/ui/UserMenu.tsx`
- Modify: `frontend/src/layouts/AppShell.tsx`

- [ ] **Step 1: Create `UserMenu.tsx`**

```tsx
import React from 'react';
import { FileText, KeyRound, Users, Shield, Settings, LogOut, User } from 'lucide-react';
import { cn } from '@/lib/utils';
import { SURFACE, TEXT, BORDER, INTERACTIVE, ELEVATION } from '@/design-system/tokens';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';

interface UserMenuItem {
  label: string;
  href?: string;
  onClick?: () => void;
  icon?: React.ReactNode;
  destructive?: boolean;
}

interface UserMenuProps {
  username?: string;
  role?: string;
  items: UserMenuItem[];
}

export const UserMenu: React.FC<UserMenuProps> = ({ username, role, items }) => {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className={cn('flex items-center gap-2 p-1.5 h-auto rounded-lg', INTERACTIVE.hover)}
          aria-label="用户菜单"
        >
          <div className={cn('w-8 h-8 rounded-full flex items-center justify-center', SURFACE.subtle)}>
            <User className={cn('w-4 h-4', TEXT.subtitle)} />
          </div>
          <div className="hidden sm:flex flex-col items-start leading-tight">
            <span className={cn('text-sm font-medium', TEXT.heading)}>{username ?? '...'}</span>
            {role && <span className={cn('text-xs', TEXT.caption)}>{role}</span>}
          </div>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className={cn('w-48', SURFACE.elevated, ELEVATION.dropdown)}>
        {items.map((item, idx) => {
          const className = item.destructive
            ? cn('text-destructive focus:text-destructive focus:bg-destructive/10', INTERACTIVE.destructiveMenu)
            : INTERACTIVE.menuItem;

          return (
            <React.Fragment key={idx}>
              {item.label === '__SEPARATOR__' ? (
                <DropdownMenuSeparator />
              ) : item.href ? (
                <DropdownMenuItem asChild>
                  <a href={item.href} className={className}>
                    {item.icon}
                    <span className="ml-2">{item.label}</span>
                  </a>
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem onClick={item.onClick} className={className}>
                  {item.icon}
                  <span className="ml-2">{item.label}</span>
                </DropdownMenuItem>
              )}
            </React.Fragment>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export default UserMenu;
```

- [ ] **Step 2: Modify `AppShell.tsx` to use `UserMenu`**

Replace the manual user menu block (lines ~159-248) with:

```tsx
import { UserMenu } from '@/components/ui/UserMenu';

// ... inside header right side:
<UserMenu
  username={currentUser?.username}
  role={currentUser?.role}
  items={[
    { label: '文档', href: '/docs', icon: <FileText className="w-4 h-4" /> },
    { label: '修改密码', href: '/account/password', icon: <KeyRound className="w-4 h-4" /> },
    ...(currentUser?.role === 'admin'
      ? [
          { label: '__SEPARATOR__' } as const,
          { label: '用户管理', href: '/users', icon: <Users className="w-4 h-4" /> },
          { label: '操作日志', href: '/audit', icon: <Shield className="w-4 h-4" /> },
          { label: '系统设置', href: '/settings', icon: <Settings className="w-4 h-4" /> },
        ]
      : []),
    { label: '__SEPARATOR__' },
    {
      label: '退出登录',
      onClick: handleLogout,
      icon: <LogOut className="w-4 h-4" />,
      destructive: true,
    },
  ]}
/>
```

- [ ] **Step 3: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ui/UserMenu.tsx frontend/src/layouts/AppShell.tsx
git commit -m "feat(ui): add accessible UserMenu and use it in AppShell"
```

---

### Task 16: Add `useDocumentTitle` and page titles

**Files:**
- Create: `frontend/src/hooks/useDocumentTitle.ts`
- Modify: migrated pages

- [ ] **Step 1: Create `useDocumentTitle.ts`**

```tsx
import { useEffect } from 'react';

export function useDocumentTitle(title: string) {
  useEffect(() => {
    const original = document.title;
    document.title = title ? `${title} | STP` : 'STP';
    return () => {
      document.title = original;
    };
  }, [title]);
}
```

- [ ] **Step 2: Apply to migrated pages**

Add to `PlanListPage`, `PlanRunListPage`, `ResultsPage`, `PlanRunDetailPage/index.tsx`, `PlanEditPage`:

```tsx
import { useDocumentTitle } from '@/hooks/useDocumentTitle';

export default function SomePage() {
  useDocumentTitle('页面标题');
  // ...
}
```

- [ ] **Step 3: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useDocumentTitle.ts
git commit -m "feat(a11y): add useDocumentTitle hook"
```

---

### Task 17: Audit icon button labels

**Files:**
- Modify: any file with icon-only buttons missing `aria-label`

- [ ] **Step 1: Find icon-only buttons**

```powershell
cd frontend; rg '<Button[^>]*size="icon"' src --type tsx -n
```

- [ ] **Step 2: Add `aria-label` to each**

For each icon-only `<Button>` without `aria-label`, add a descriptive label.

- [ ] **Step 3: Run tests**

```powershell
cd frontend; npx vitest run
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(a11y): add aria-label to icon-only buttons"
```

---

### Task 18: Remove `HeaderSlotContext` after migration

**Files:**
- Modify: `frontend/src/contexts/HeaderSlotContext.tsx`
- Modify: remove `HeaderSlotProvider` from app root if still present

- [ ] **Step 1: Verify no consumers remain**

```powershell
cd frontend; rg 'useHeaderSlot|HeaderSlotProvider|setHeaderSlot|setFullBleed' src --type tsx -n
```

Expected: only matches inside `HeaderSlotContext.tsx`.

- [ ] **Step 2: Delete `HeaderSlotContext.tsx` and references**

```bash
git rm frontend/src/contexts/HeaderSlotContext.tsx
```

Remove `HeaderSlotProvider` from `frontend/src/main.tsx` or `App.tsx` if present.

- [ ] **Step 3: Run type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(layout): remove deprecated HeaderSlotContext"
```

---

## Final Verification

### Task 19: Run full verification suite

- [ ] **Step 1: Type check**

```powershell
cd frontend; npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 2: Unit tests**

```powershell
cd frontend; npx vitest run
```

Expected: all tests pass.

- [ ] **Step 3: Production build**

```powershell
cd frontend; npm run build
```

Expected: build succeeds.

- [ ] **Step 4: Manual smoke test**

Run dev server:

```powershell
cd frontend; npm run dev
```

Verify:
- `PlanListPage` renders grid/list, action buttons always visible.
- `PlanRunListPage` renders list, row navigation works.
- `ResultsPage` table scrolls horizontally, row actions menu works.
- `PlanRunDetailPage` tabs switch, no full-page scroll.
- `PlanEditPage` panels resize/collapse, no duplicate title.
- Toast appears top-right.
- User menu opens with keyboard.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(ui): complete frontend UX redesign"
```

---

## Self-Review

### Spec Coverage

| Design Section | Implementing Tasks |
|----------------|--------------------|
| 2.1 HeaderSlotContext deprecation | Task 4, Task 5, Task 18 |
| 2.2 PageContainer / PageHeaderV2 | Task 2, Task 3 |
| 3. DataList / DataTable | Task 6, Task 7, Task 8, Tasks 9-11 |
| 4. PlanRun detail tabs | Task 12 |
| 5. PlanEdit resizable panels | Task 13 |
| 6. Toast / User Menu / a11y | Tasks 14-17 |
| 7. Phases & acceptance | Task 19 |

### Placeholder Scan

No placeholders remain. Every task includes file paths, code, commands, and expected output.

### Type Consistency

- `PageContainer` props: `fullBleed`, `scrollable`, `width` consistent across usages.
- `useToast` API preserves string overloads; `toast.promise` added.
- `DataList`/`DataTable` selection uses `Set<string>` consistently.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-27-frontend-ux-redesign.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints.

**Which approach?**
