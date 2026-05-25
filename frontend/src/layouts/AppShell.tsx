import { useState, useEffect, Suspense } from 'react';
import { Outlet, useNavigate, NavLink } from 'react-router-dom';
import Sidebar from './Sidebar';
import { Menu, ChevronRight, FileText, LogOut, User, ChevronDown, Loader2, KeyRound } from 'lucide-react';
import { cn } from '@/lib/utils';
import { clearAppQueryCache } from '@/components/QueryProvider';
import { disconnectDashSocket } from '@/hooks/useSocketIO';
import { useAuthSession } from '@/hooks/useAuthSession';
import { api } from '@/utils/api';

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
    <div className="flex h-screen bg-gray-50">
      {/* 移动端遮罩层 */}
      {isMobile && sidebarOpen && (
        <div
          className="fixed inset-0 bg-gray-900/40 z-40 transition-opacity duration-300"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar - 桌面端 */}
      <aside
        className={cn(
          "hidden lg:flex flex-col border-r border-gray-100 bg-white",
          "transition-all duration-300"
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
          "fixed inset-y-0 left-0 z-50 w-56 bg-white border-r border-gray-100 transform transition-transform duration-300 lg:hidden",
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
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
          className="fixed left-[60px] top-1/2 -translate-y-1/2 z-40 h-6 w-6 rounded-full border border-gray-200 bg-white shadow-sm hover:bg-gray-50 flex items-center justify-center transition-all duration-200"
        >
          <ChevronRight size={14} className="text-gray-500" />
        </button>
      )}

      {/* 主内容区 - 样板风格 */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Header - 样板风格 */}
        <header className="sticky top-0 z-30 bg-white/80 backdrop-blur-sm border-b border-gray-100">
          <div className="flex items-center justify-between h-16 px-4 lg:px-8">
            <button
              onClick={toggleSidebar}
              className="lg:hidden p-2 text-gray-400 hover:text-gray-600"
            >
              <Menu className="w-5 h-5" />
            </button>

            <div className="flex-1" />

            {/* Right side: User Menu */}
            <div className="flex items-center gap-2">
              {/* User Menu - Top Right Corner */}
              <div className="relative ml-2">
                <button
                  onClick={() => setShowUserMenu(!showUserMenu)}
                  className="flex items-center gap-2 p-1.5 rounded-lg hover:bg-gray-50 transition-colors"
                  aria-label="用户菜单"
                >
                  <div className="w-8 h-8 bg-gray-100 rounded-full flex items-center justify-center">
                    <User className="w-4 h-4 text-gray-500" />
                  </div>
                  <div className="hidden sm:flex flex-col items-start leading-tight">
                    <span className="text-sm font-medium text-gray-900">
                      {currentUser?.username ?? '...'}
                    </span>
                    {currentUser?.role && (
                      <span className="text-xs text-gray-400">{currentUser.role}</span>
                    )}
                  </div>
                  <ChevronDown className={cn(
                    "w-4 h-4 text-gray-400 transition-transform hidden sm:block",
                    showUserMenu && "rotate-180"
                  )} />
                </button>

                {/* Dropdown Menu */}
                {showUserMenu && (
                  <>
                    <div
                      className="fixed inset-0 z-10"
                      onClick={() => setShowUserMenu(false)}
                    />
                    <div className="absolute right-0 top-full mt-1 w-48 bg-white rounded-lg shadow-lg border border-gray-100 py-1 z-20">
                      <a
                        href="/docs"
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={() => setShowUserMenu(false)}
                        className="flex items-center gap-3 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50"
                      >
                        <FileText className="w-4 h-4" />
                        文档
                      </a>
                      <NavLink
                        to="/account/password"
                        onClick={() => setShowUserMenu(false)}
                        className="flex items-center gap-3 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50"
                      >
                        <KeyRound className="w-4 h-4" />
                        修改密码
                      </NavLink>
                      <hr className="my-1 border-gray-100" />
                      <button
                        onClick={handleLogout}
                        className="w-full flex items-center gap-3 px-4 py-2 text-sm text-red-600 hover:bg-red-50"
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

        {/* 主内容区 */}
        <main className="flex-1 overflow-x-hidden overflow-y-auto">
          <div className="p-4 lg:p-8">
            <Suspense fallback={
              <div className="flex items-center justify-center h-64">
                <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
              </div>
            }>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>
    </div>
  );
}
