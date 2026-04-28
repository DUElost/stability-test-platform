import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ScriptEntry } from '@/utils/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { formatLocalDateTime } from '@/utils/time';
import { FileCode2, RefreshCw, Search } from 'lucide-react';

function paramKeys(schema: Record<string, any> | undefined | null): string[] {
  if (!schema) return [];
  const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : schema;
  return Object.keys(properties).slice(0, 8);
}

export default function ScriptLibraryPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string | null>(null);

  const { data: scripts = [], isLoading } = useQuery({
    queryKey: ['scripts', 'library'],
    queryFn: () => api.scripts.list(true),
  });
  const { data: categories = [] } = useQuery({
    queryKey: ['scripts', 'categories'],
    queryFn: () => api.scripts.listCategories(),
  });
  const { data: sequences } = useQuery({
    queryKey: ['script-sequences', 'library'],
    queryFn: () => api.scriptSequences.list(0, 100),
  });

  const referenceCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const sequence of sequences?.items || []) {
      const seen = new Set(sequence.items.map((item) => `${item.script_name}:${item.version}`));
      seen.forEach((key) => counts.set(key, (counts.get(key) || 0) + 1));
    }
    return counts;
  }, [sequences]);

  const scanMutation = useMutation({
    mutationFn: () => api.scripts.scan(),
    onSuccess: (result) => {
      toast.success(`扫描完成：新增 ${result.created} 个，跳过 ${result.skipped} 个，停用 ${result.deactivated} 个`);
      queryClient.invalidateQueries({ queryKey: ['scripts'] });
    },
    onError: () => toast.error('扫描脚本失败'),
  });

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return scripts.filter((script: ScriptEntry) => {
      const inCategory = !category || script.category === category;
      const inQuery = !q
        || script.name.toLowerCase().includes(q)
        || (script.display_name ?? '').toLowerCase().includes(q)
        || (script.description ?? '').toLowerCase().includes(q);
      return inCategory && inQuery;
    });
  }, [scripts, query, category]);

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">脚本库</h1>
          <p className="mt-1 text-sm text-gray-500">浏览 NFS 脚本元数据、版本和参数定义</p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => scanMutation.mutate()}
          disabled={scanMutation.isLoading}
        >
          <RefreshCw className={`mr-2 h-4 w-4 ${scanMutation.isLoading ? 'animate-spin' : ''}`} />
          重新扫描
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">分类</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <button
              type="button"
              onClick={() => setCategory(null)}
              className={`w-full rounded-md px-3 py-2 text-left text-sm transition-colors ${
                category === null ? 'bg-gray-900 text-white' : 'text-gray-700 hover:bg-gray-100'
              }`}
            >
              全部 ({scripts.length})
            </button>
            {categories.map((item) => (
              <button
                type="button"
                key={item}
                onClick={() => setCategory(item)}
                className={`w-full rounded-md px-3 py-2 text-left text-sm transition-colors ${
                  category === item ? 'bg-gray-900 text-white' : 'text-gray-700 hover:bg-gray-100'
                }`}
              >
                {item}
              </button>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <CardTitle className="text-base">脚本列表</CardTitle>
              <label className="relative block w-full md:w-72">
                <span className="sr-only">搜索脚本</span>
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  className="w-full rounded-md border border-gray-200 py-2 pl-9 pr-3 text-sm outline-none focus:ring-2 focus:ring-gray-900/10"
                  placeholder="搜索脚本名称或描述"
                />
              </label>
            </div>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 4 }).map((_, index) => (
                  <Skeleton key={index} className="h-20 w-full" />
                ))}
              </div>
            ) : filtered.length === 0 ? (
              <div className="rounded-md border border-dashed py-10 text-center text-sm text-gray-500">暂无脚本</div>
            ) : (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {filtered.map((script) => (
                  <div key={script.id} className="rounded-md border border-gray-200 bg-white p-4">
                    <div className="flex items-start gap-3">
                      <div className="rounded-md bg-gray-100 p-2">
                        <FileCode2 className="h-4 w-4 text-gray-600" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium text-gray-900">{script.name}</div>
                        <div className="mt-1 text-xs text-gray-500">
                          v{script.version} · {script.script_type} · {script.category || '未分类'}
                        </div>
                      </div>
                    </div>
                    {script.description && (
                      <p className="mt-3 line-clamp-2 text-sm text-gray-600">{script.description}</p>
                    )}
                    {paramKeys(script.param_schema).length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-1">
                        {paramKeys(script.param_schema).map((key) => (
                          <span key={key} className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-600">
                            {key}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-gray-500">
                      <span>{referenceCounts.get(`${script.name}:${script.version}`) || 0} 个模板引用</span>
                      {script.updated_at && <span>更新 {formatLocalDateTime(script.updated_at)}</span>}
                    </div>
                    <div className="mt-3 truncate font-mono text-xs text-gray-400" title={script.nfs_path}>
                      {script.nfs_path}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
