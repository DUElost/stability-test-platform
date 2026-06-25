import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useToast } from '@/components/ui/toast';
import { api, type ScriptEntry } from '@/utils/api';
import { Search, FileCode, Code2, Tag, RefreshCw } from 'lucide-react';
import ScriptVersionDialog from './ScriptVersionDialog';
import { PageContainer, PageHeader } from '@/components/layout';
import { LoadingGrid, CardSkeleton } from '@/components/ui/loading-skeleton';
import { EmptyState, SearchEmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

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
    <PageContainer width="list">
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
          <Search className={cn('absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4', TEXT.subtitle)} />
          <Input type="text" placeholder="搜索脚本名称、分类、类型..." value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9" />
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
        <LoadingGrid count={2} columns={1} component={CardSkeleton} />
      ) : isError ? (
        <ErrorState
          title="加载脚本列表失败"
          description={(error as Error)?.message || '请检查后端服务是否正常'}
          onRetry={() => queryClient.invalidateQueries({ queryKey: ['scripts-active'] })}
        />
      ) : filtered.length === 0 ? (
        search ? (
          <SearchEmptyState keyword={search} />
        ) : (
          <EmptyState
            title="暂无脚本"
            description="请通过脚本目录扫描入库"
            icon={<FileCode className="w-16 h-16" />}
          />
        )
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
                        <h3 className={cn('font-medium', TEXT.heading)}>{script.name}</h3>
                        <span className={cn('text-xs px-1.5 py-0.5 rounded font-mono', STATUS_CHIP.muted)}>{script.version}</span>
                        <span className={cn('text-xs px-1.5 py-0.5 rounded', 
                          script.category === 'device' ? STATUS_CHIP.primary :
                          script.category === 'host' ? STATUS_CHIP.success :
                          STATUS_CHIP.muted
                        )}>{script.category || '未分类'}</span>
                        <span className={cn('text-xs', TEXT.subtitle)}>{script.script_type}</span>
                      </div>
                      {script.display_name && <p className={cn('text-sm mt-0.5', TEXT.subtitle)}>{script.display_name}</p>}
                      <p className={cn('text-xs mt-1 truncate', TEXT.subtitle)}>{script.nfs_path}</p>
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
                    <div className="mt-3 p-3 bg-muted/50 rounded-lg text-xs space-y-2">
                      {script.default_params && Object.keys(script.default_params).length > 0 && (
                        <div>
                          <span className={cn('font-medium', TEXT.subtitle)}>默认参数: </span>
                          <code className={TEXT.body}>{JSON.stringify(script.default_params, null, 2)}</code>
                        </div>
                      )}
                      {script.param_schema && Object.keys(script.param_schema).length > 0 && (
                        <div>
                          <span className={cn('font-medium', TEXT.subtitle)}>参数 Schema: </span>
                          <code className={TEXT.body}>{JSON.stringify(script.param_schema, null, 2)}</code>
                        </div>
                      )}
                      {(!script.default_params || Object.keys(script.default_params).length === 0) &&
                       (!script.param_schema || Object.keys(script.param_schema).length === 0) && (
                        <p className={TEXT.subtitle}>无参数定义</p>
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
