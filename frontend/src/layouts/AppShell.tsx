import { useState, useEffect, Suspense } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import Sidebar from './Sidebar';
import { Menu, ChevronRight, FileText, LogOut, Loader2, KeyRound, Wifi, WifiOff, Users, Shield, Settings } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { clearAppQueryCache } from '@/components/QueryProvider';
import { useSocketIO, disconnectDashSocket } from '@/hooks/useSocketIO';
import { useAuthSession } from '@/hooks/useAuthSession';
import { UserMenu } from '@/components/ui/UserMenu';
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
