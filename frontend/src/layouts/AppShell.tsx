import { useState, useEffect, useRef, Suspense } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import Sidebar from './Sidebar';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Menu, FileText, LogOut, Loader2, KeyRound, WifiOff, Users, Shield, Settings, BellRing } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { clearAppQueryCache } from '@/components/QueryProvider';
import { useSocketIO, disconnectDashSocket } from '@/hooks/useSocketIO';
import { useCrossClientSync } from '@/hooks/useCrossClientSync';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useHeaderSlot } from '@/contexts/HeaderSlotContext';
import { UserMenu } from '@/components/ui/UserMenu';
import { NotificationBell } from '@/components/ui/NotificationBell';
import { ThemeToggle } from '@/components/ui/ThemeToggle';
import { api } from '@/utils/api';
import { DASHBOARD_SUBSCRIPTION } from '@/config';
import { BORDER, INTERACTIVE, SIDEBAR, SURFACE, TEXT } from '@/design-system/tokens';

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'stp_sidebar_collapsed';

function readInitialSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

/**
 * 主应用布局 - 源自 web 样板设计风格
 */
export default function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(readInitialSidebarCollapsed);
  const [isMobile, setIsMobile] = useState(false);
  // #1197：移动抽屉关闭后把焦点还给触发按钮（链接已 inert，不能原地留焦）
  const sidebarToggleRef = useRef<HTMLButtonElement | null>(null);
  const wasSidebarOpenRef = useRef(false);
  const navigate = useNavigate();
  const location = useLocation();
  const sessionQ = useAuthSession();
  const currentUser = sessionQ.data;
  const { isConnected: dashConnected } = useSocketIO(DASHBOARD_SUBSCRIPTION, {
    trackLastMessage: false,
  });
  const { headerSlot } = useHeaderSlot();
  useCrossClientSync();

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
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, []);

  // #1197：移动抽屉从开到关时，焦点回到触发按钮（关闭按钮/遮罩/Escape 三条路径共用）
  useEffect(() => {
    if (isMobile && wasSidebarOpenRef.current && !sidebarOpen) {
      sidebarToggleRef.current?.focus();
    }
    wasSidebarOpenRef.current = sidebarOpen;
  }, [isMobile, sidebarOpen]);

  const toggleSidebar = () => setSidebarOpen(!sidebarOpen);
  const toggleSidebarCollapse = () => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, next ? '1' : '0');
      } catch {
        // ignore storage errors in private mode
      }
      return next;
    });
  };

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
          'hidden lg:flex flex-col transition-all duration-200 border-r shrink-0',
          SIDEBAR.root,
          SIDEBAR.border,
        )}
        style={{ width: sidebarCollapsed ? 56 : 208 }}
      >
        <Sidebar
          onNavigate={() => isMobile && setSidebarOpen(false)}
          collapsed={sidebarCollapsed}
          onToggleCollapse={toggleSidebarCollapse}
        />
      </aside>

      <aside
        data-testid="mobile-drawer"
        // #1197：关闭态抽屉仅位移动画隐藏，链接仍在 DOM——inert 让整棵子树退出焦点顺序
        inert={!sidebarOpen}
        className={cn(
          'fixed inset-y-0 left-0 z-50 w-52 transform transition-transform duration-300 lg:hidden border-r',
          SIDEBAR.root,
          SIDEBAR.border,
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

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header
          data-testid="app-shell-header"
          className={cn('sticky top-0 z-30 shrink-0 border-b', SURFACE.header, BORDER.default)}
        >
          <div className="flex items-center justify-between h-12 px-4 lg:px-5 gap-2">
            <button
              ref={sidebarToggleRef}
              onClick={toggleSidebar}
              className={cn('lg:hidden p-1.5', INTERACTIVE.iconButton)}
              aria-label="打开侧边栏"
            >
              <Menu className="w-5 h-5" />
            </button>

            <div className="flex flex-1 items-center min-w-0">
              {headerSlot}
            </div>

            {headerSlot && (
              <div
                className="mx-1.5 hidden h-4 w-px shrink-0 bg-border sm:block"
                aria-hidden="true"
              />
            )}

            <div className="flex shrink-0 items-center gap-1.5">
              {dashConnected ? (
                <span
                  data-testid="ws-connection-status"
                  title="实时数据通道已连接"
                  aria-label="实时数据通道已连接"
                  className="hidden sm:inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground"
                >
                  <span className="h-2 w-2 rounded-full bg-success ring-2 ring-success/20" />
                </span>
              ) : (
                <Badge
                  data-testid="ws-connection-status"
                  variant="destructive"
                  className="hidden gap-1 px-2 py-0.5 text-[11px] sm:inline-flex"
                  title="实时连接已断开"
                >
                  <WifiOff size={12} />
                  已断开
                </Badge>
              )}

              <ThemeToggle />

              <NotificationBell />

              <UserMenu
                username={currentUser?.username}
                role={currentUser?.role}
                items={[
                  { label: '文档', href: '/docs', external: true, icon: <FileText className="w-4 h-4" /> },
                  { label: '修改密码', href: '/account/password', icon: <KeyRound className="w-4 h-4" /> },
                  // FRONTEND_NAV_IA_REDESIGN v1.3：低频 admin 管理页收进下拉（三级入口）
                  ...(currentUser?.role === 'admin'
                    ? [
                        { label: '__SEPARATOR__' } as const,
                        { label: '用户管理', href: '/users', icon: <Users className="w-4 h-4" /> },
                        { label: '通知管理', href: '/notifications', icon: <BellRing className="w-4 h-4" /> },
                        { label: '审计日志', href: '/audit', icon: <Shield className="w-4 h-4" /> },
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
            </div>
          </div>
        </header>

        <main className="flex-1 min-h-0 overflow-hidden">
          {/* H5'：ErrorBoundary 下沉到路由层——单页 render-throw 只崩内容区，
              侧栏/顶栏（AppShell 外壳）保住；App.tsx 顶层 ErrorBoundary 仍为
              Provider/Shell 级最后防线。#821：resetKey=pathname——切路由即
              复位错误屏，不再全站停留到整页刷新 */}
          <ErrorBoundary fullscreen={false} resetKey={location.pathname}>
            <Suspense fallback={
              <div className="flex items-center justify-center h-64">
                <Loader2 className={cn('w-8 h-8 animate-spin', TEXT.caption)} />
              </div>
            }>
              <Outlet />
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
