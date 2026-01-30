import { Bell, Menu } from 'lucide-react';

interface HeaderProps {
  onMenuClick?: () => void;
  showMenuButton?: boolean;
}

export default function Header({ onMenuClick, showMenuButton = false }: HeaderProps) {
  return (
    <header className="bg-white border-b border-slate-200 h-16 px-4 sm:px-6 flex items-center justify-between shrink-0">
      <div className="flex items-center gap-3">
        {showMenuButton && (
          <button
            onClick={onMenuClick}
            className="lg:hidden p-2 text-slate-500 hover:bg-slate-100 rounded-lg transition-colors"
            aria-label="Toggle menu"
          >
            <Menu size={20} />
          </button>
        )}
        <h2 className="text-lg font-semibold text-slate-800">Stability Test Platform</h2>
      </div>

      <div className="flex items-center gap-4">
        <button className="p-2 text-slate-500 hover:bg-slate-100 rounded-full relative" aria-label="Notifications">
          <Bell size={20} />
          <span className="absolute top-2 right-2 w-2 h-2 bg-red-500 rounded-full border border-white"></span>
        </button>

        <div className="flex items-center gap-2 pl-4 border-l border-slate-200">
          <div className="w-8 h-8 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center font-medium">
            A
          </div>
        </div>
      </div>
    </header>
  );
}
