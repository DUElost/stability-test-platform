import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Smartphone, ListTodo, Server } from 'lucide-react';
import { clsx } from 'clsx';

const navItems = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/hosts', label: 'Hosts', icon: Server },
  { path: '/devices', label: 'Devices', icon: Smartphone },
  { path: '/tasks', label: 'Tasks', icon: ListTodo },
];

interface SidebarProps {
  onNavigate?: () => void;
}

export default function Sidebar({ onNavigate }: SidebarProps) {
  return (
    <nav className="flex-1 py-6 px-3 space-y-1">
      {navItems.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          onClick={onNavigate}
          className={({ isActive }) =>
            clsx(
              'flex items-center gap-3 px-3 py-2 rounded-md transition-all duration-200 text-sm font-medium',
              isActive
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-slate-400 hover:bg-slate-800 hover:text-white'
            )
          }
        >
          <item.icon size={20} />
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
