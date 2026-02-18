import React, { useMemo, useState } from 'react';
import { Tool, ToolCategory } from '../../utils/api';
import { Search } from 'lucide-react';

interface ToolSelectorProps {
  tools: Tool[];
  categories: ToolCategory[];
  selectedToolId: number | null;
  onSelect: (tool: Tool) => void;
}

export const ToolSelector: React.FC<ToolSelectorProps> = ({ tools, categories, selectedToolId, onSelect }) => {
  const [search, setSearch] = useState('');
  const [selectedCatId, setSelectedCatId] = useState<number | null>(null);

  const filteredTools = useMemo(() => {
    return tools.filter(tool => {
      const matchSearch = tool.name.toLowerCase().includes(search.toLowerCase()) || 
                          (tool.description || '').toLowerCase().includes(search.toLowerCase());
      const matchCat = selectedCatId === null || tool.category_id === selectedCatId;
      return matchSearch && matchCat && tool.enabled;
    });
  }, [tools, search, selectedCatId]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col md:flex-row gap-3">
        {/* Search */}
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
          <input
            type="text"
            placeholder="Search tools..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
          />
        </div>
        
        {/* Category Filter */}
        <div className="flex gap-2 overflow-x-auto pb-1 no-scrollbar">
          <button
            onClick={() => setSelectedCatId(null)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-colors ${
              selectedCatId === null ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            All
          </button>
          {categories.map(cat => (
            <button
              key={cat.id}
              onClick={() => setSelectedCatId(cat.id)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-colors ${
                selectedCatId === cat.id ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              {cat.name}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 max-h-[400px] overflow-y-auto pr-1">
        {filteredTools.length === 0 ? (
          <div className="col-span-full py-10 text-center text-slate-400 bg-slate-50 rounded-lg border border-dashed border-slate-200">
            未找到匹配的工具。
          </div>
        ) : (
          filteredTools.map(tool => (
            <button
              key={tool.id}
              type="button"
              onClick={() => onSelect(tool)}
              className={`flex flex-col text-left p-4 rounded-lg border transition-all ${
                selectedToolId === tool.id
                  ? 'border-indigo-600 bg-indigo-50 ring-2 ring-indigo-100'
                  : 'border-slate-200 hover:border-indigo-300 bg-white shadow-sm'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-500">
                  {tool.category_name}
                </span>
                {selectedToolId === tool.id && (
                  <div className="w-4 h-4 rounded-full bg-indigo-600 flex items-center justify-center">
                    <div className="w-1.5 h-1.5 rounded-full bg-white" />
                  </div>
                )}
              </div>
              <h4 className="font-semibold text-slate-900 text-sm mb-1">{tool.name}</h4>
              <p className="text-xs text-slate-500 line-clamp-2 flex-1">
                {tool.description || 'No description provided.'}
              </p>
              <div className="mt-3 pt-2 border-t border-slate-100 flex items-center justify-between text-[10px] text-slate-400 font-mono">
                <span>{tool.script_type}</span>
                <span>{tool.timeout}s timeout</span>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
};
