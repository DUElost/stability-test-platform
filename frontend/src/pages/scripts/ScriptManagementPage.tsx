import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { api, type ScriptEntry } from '@/utils/api';
import { Search, FileCode, Code2, Tag, RefreshCw, AlertCircle } from 'lucide-react';
import ScriptVersionDialog from './ScriptVersionDialog';
import { PageContainer, PageHeader } from '@/components/layout';

export default function ScriptManagementPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [search, setSearch] = useState('');
  const [showVersionDialog, setShowVersionDialog] = useState(false);
  const [versionTarget, setVersionTarget] = useState<ScriptEntry | null>(null);
  const [showJson, setShowJson] = useState<Record<string, boolean>>({});

  const { data: scripts, isLoading, isError, error } = useQuery({
    queryKey: ['scripts-active'],
    queryFn: () => api.scripts.list(true),
  });

  const scanMut = useMutation({
    mutationFn: () => api.scripts.scan(),
    onSuccess: (result) => {
      toast.success(
        `扫描完成: 新增 ${result.created}, 跳过 ${result.skipped}, 停用 ${result.deactivated}` +
        (result.conflicts.length ? `, 冲突 ${result.conflicts.length}` : ''),
      );
      queryClient.invalidateQueries({ queryKey: ['scripts-active'] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`扫描失败: ${msg}`);
    },
  });

  const filtered = useMemo(() => {
    if (!scripts) return [];
    const q = search.toLowerCase();
    return scripts.filter(s =>
      !q || s.name.toLowerCase().includes(q) ||
      (s.category || '').toLowerCase().includes(q) ||
      (s.script_type || '').toLowerCase().includes(q)
    );
  }, [scripts, search]);

  const toggleJson = (key: string) => {
    setShowJson(prev => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <PageContainer className="max-w-5xl">
      <ScriptVersionDialog
        open={showVersionDialog}
        script={versionTarget}
        onClose={() => { setShowVersionDialog(false); setVersionTarget(null); }}
        onCreated={() => {
          queryClient.invalidateQueries({ queryKey: ['scripts-active'] });
          setShowVersionDialog(false);
          setVersionTarget(null);
        }}
      />

      <PageHeader title="脚本管理" subtitle="管理脚本目录、默认参数与版本。修改默认参数需创建新版本。" />

      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input type="text" placeholder="搜索脚本名称、分类、类型..." value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20" />
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={scanMut.isPending}
          onClick={() => scanMut.mutate()}
        >
          <RefreshCw className={`w-4 h-4 mr-1.5 ${scanMut.isPending ? 'animate-spin' : ''}`} />
          {scanMut.isPending ? '扫描中…' : '扫描脚本目录'}
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-3"><Skeleton className="h-24 w-full" /><Skeleton className="h-24 w-full" /></div>
      ) : isError ? (
        <Card><CardContent className="py-12 text-center text-gray-400">
          <AlertCircle className="w-10 h-10 mx-auto mb-3 text-red-300" />
          <p className="text-sm text-red-600 font-medium">加载脚本列表失败</p>
          <p className="text-xs text-gray-400 mt-1">{(error as Error)?.message || '请检查后端服务是否正常'}</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={() => queryClient.invalidateQueries({ queryKey: ['scripts-active'] })}>
            <RefreshCw className="w-3.5 h-3.5 mr-1" />重试
          </Button>
        </CardContent></Card>
      ) : filtered.length === 0 ? (
        <Card><CardContent className="py-12 text-center text-gray-400">
          <FileCode className="w-10 h-10 mx-auto mb-3 text-gray-300" />
          <p className="text-sm">{search ? '没有匹配的脚本' : '暂无脚本，请通过脚本目录扫描入库'}</p>
        </CardContent></Card>
      ) : (
        <div className="space-y-3">
          {filtered.map(script => {
            const key = `${script.name}:${script.version}`;
            const expanded = showJson[key];

            return (
              <Card key={key}>
                <CardContent className="py-4">
                  <div className="flex items-start justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="font-medium text-gray-900">{script.name}</h3>
                        <span className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded font-mono">{script.version}</span>
                        <span className={`text-xs px-1.5 py-0.5 rounded ${
                          script.category === 'device' ? 'bg-blue-100 text-blue-700' :
                          script.category === 'host' ? 'bg-green-100 text-green-700' :
                          'bg-gray-100 text-gray-600'
                        }`}>{script.category || '未分类'}</span>
                        <span className="text-xs text-gray-400">{script.script_type}</span>
                      </div>
                      {script.display_name && <p className="text-sm text-gray-600 mt-0.5">{script.display_name}</p>}
                      <p className="text-xs text-gray-400 mt-1 truncate">{script.nfs_path}</p>
                    </div>
                    <div className="flex items-center gap-1 ml-4">
                      <Button variant="ghost" size="sm" onClick={() => toggleJson(key)} title="参数详情">
                        <Code2 className="w-4 h-4" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => { setVersionTarget(script); setShowVersionDialog(true); }} title="新建版本">
                        <Tag className="w-4 h-4" />
                      </Button>
                    </div>
                  </div>

                  {/* Expandable params */}
                  {expanded && (
                    <div className="mt-3 p-3 bg-gray-50 rounded-lg text-xs space-y-2">
                      {script.default_params && Object.keys(script.default_params).length > 0 && (
                        <div>
                          <span className="font-medium text-gray-600">默认参数: </span>
                          <code className="text-gray-800">{JSON.stringify(script.default_params, null, 2)}</code>
                        </div>
                      )}
                      {script.param_schema && Object.keys(script.param_schema).length > 0 && (
                        <div>
                          <span className="font-medium text-gray-600">参数 Schema: </span>
                          <code className="text-gray-800">{JSON.stringify(script.param_schema, null, 2)}</code>
                        </div>
                      )}
                      {(!script.default_params || Object.keys(script.default_params).length === 0) &&
                       (!script.param_schema || Object.keys(script.param_schema).length === 0) && (
                        <p className="text-gray-400">无参数定义</p>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </PageContainer>
  );
}
