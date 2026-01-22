import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Smartphone, ListTodo, Activity } from 'lucide-react';
import { clsx } from 'clsx';

const navItems = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/tasks', label: 'Tasks', icon: ListTodo },
  { path: '/devices', label: 'Devices', icon: Smartphone },
];

export default function Sidebar() {
  return (
    <aside className="w-64 bg-slate-900 text-white flex flex-col">
      <div className="h-16 flex items-center px-6 border-b border-slate-800">
        <Activity className="text-blue-500 mr-2" />
        <span className="font-bold text-lg">StabilityPro</span>
      </div>

      <nav className="flex-1 py-6 px-3 space-y-1">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-3 px-3 py-2 rounded-md transition-colors text-sm font-medium',
                isActive ? 'bg-blue-600 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-white'
              )
            }
          >
            <item.icon size={20} />
            {item.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
