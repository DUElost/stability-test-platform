import { Bell } from 'lucide-react';

export default function Header() {
  return (
    <header className="bg-white border-b border-slate-200 h-16 px-6 flex items-center justify-between">
      <h2 className="text-lg font-semibold text-slate-800">Stability Test Platform</h2>

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
