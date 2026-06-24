import { useState, useEffect, Suspense } from 'react';
import { Outlet, useNavigate, NavLink } from 'react-router-dom';
import Sidebar from './Sidebar';
import { Menu, ChevronRight, FileText, LogOut, User, ChevronDown, Loader2, KeyRound, Wifi, WifiOff, Users, Shield, Settings } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { clearAppQueryCache } from '@/components/QueryProvider';
import { useSocketIO, disconnectDashSocket } from '@/hooks/useSocketIO';
import { useAuthSession } from '@/hooks/useAuthSession';
import { api } from '@/utils/api';
import { useHeaderSlot } from '@/contexts/HeaderSlotContext';
import { WS_DASHBOARD_ENDPOINT } from '@/config';
import { BORDER, ELEVATION, INTERACTIVE, LAYOUT, SURFACE, TEXT } from '@/design-system/tokens';

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
  const { headerSlot, fullBleed } = useHeaderSlot();
  const { isConnected: dashConnected } = useSocketIO(WS_DASHBOARD_ENDPOINT);

  // 监听窗口大小变化
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

  // ESC 键关闭侧边栏和菜单
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
      {/* 移动端遮罩层 */}
      {isMobile && sidebarOpen && (
        <div
          className={cn('fixed inset-0 z-40 transition-opacity duration-300', SURFACE.overlay)}
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar - 桌面端 */}
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

      {/* Sidebar - 移动端抽屉模式 */}
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

      {/* 桌面端悬浮展开按钮 (当侧边栏折叠时) */}
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

      {/* 主内容区 - 样板风格 */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Header - 样板风格 */}
        <header className={cn('sticky top-0 z-30 border-b', SURFACE.header, BORDER.default)}>
          <div className="flex items-center justify-between h-20 px-4 lg:px-8">
            <button
              onClick={toggleSidebar}
              className={cn('lg:hidden p-2', INTERACTIVE.iconButton)}
            >
              <Menu className="w-5 h-5" />
            </button>

            {/* 页面可通过 HeaderSlotContext 向此区域注入导航内容 */}
            <div className="flex flex-1 items-center min-w-0">
              {headerSlot}
            </div>

            {/* Right side: User Menu */}
            <div className="flex items-center gap-2">
              {/* 实时连接状态 — dashboard socket(全局),位于用户菜单左侧 */}
              <Badge
                variant={dashConnected ? 'success' : 'destructive'}
                className="hidden gap-1.5 sm:inline-flex"
                title={dashConnected ? '实时数据通道已连接' : '实时连接已断开'}
              >
                {dashConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
                {dashConnected ? '实时连接' : '已断开'}
              </Badge>
              {/* User Menu - Top Right Corner */}
              <div className="relative ml-2">
                <button
                  onClick={() => setShowUserMenu(!showUserMenu)}
                  className={cn('flex items-center gap-2 p-1.5 rounded-lg transition-colors', INTERACTIVE.hover)}
                  aria-label="用户菜单"
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

                {/* Dropdown Menu */}
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
                      <NavLink
                        to="/account/password"
                        onClick={() => setShowUserMenu(false)}
                        className={cn('flex items-center gap-3 px-4 py-2 text-sm', INTERACTIVE.menuItem)}
                      >
                        <KeyRound className="w-4 h-4" />
                        修改密码
                      </NavLink>
                      {currentUser?.role === 'admin' && (
                        <>
                          <hr className={cn('my-1', BORDER.default)} />
                          <NavLink
                            to="/users"
                            onClick={() => setShowUserMenu(false)}
                            className={cn('flex items-center gap-3 px-4 py-2 text-sm focus-visible:outline-none', INTERACTIVE.menuItem)}
                          >
                            <Users className="w-4 h-4" />
                            用户管理
                          </NavLink>
                          <NavLink
                            to="/audit"
                            onClick={() => setShowUserMenu(false)}
                            className={cn('flex items-center gap-3 px-4 py-2 text-sm focus-visible:outline-none', INTERACTIVE.menuItem)}
                          >
                            <Shield className="w-4 h-4" />
                            操作日志
                          </NavLink>
                          <NavLink
                            to="/settings"
                            onClick={() => setShowUserMenu(false)}
                            className={cn('flex items-center gap-3 px-4 py-2 text-sm focus-visible:outline-none', INTERACTIVE.menuItem)}
                          >
                            <Settings className="w-4 h-4" />
                            系统设置
                          </NavLink>
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

        {/* 主内容区 — fullBleed 时去掉内边距并锁定 overflow，页面自管滚动 */}
        {fullBleed ? (
          <main className="flex-1 overflow-hidden">
            <Suspense fallback={
              <div className="flex items-center justify-center h-64">
                <Loader2 className={cn('w-8 h-8 animate-spin', TEXT.caption)} />
              </div>
            }>
              <Outlet />
            </Suspense>
          </main>
        ) : (
          <main className="flex-1 overflow-x-hidden overflow-y-auto">
            <div className={LAYOUT.pagePadding}>
              <Suspense fallback={
                <div className="flex items-center justify-center h-64">
                  <Loader2 className={cn('w-8 h-8 animate-spin', TEXT.caption)} />
                </div>
              }>
                <Outlet />
              </Suspense>
            </div>
          </main>
        )}
      </div>
    </div>
  );
}
