import { useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  CheckCircle2,
  Download,
  FileUp,
  FolderOutput,
  Pencil,
  Plus,
  Trash2,
  Upload,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { PageContainer } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useToast } from '@/hooks/useToast';
import { ALERT_BOX, FORM, STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { api, toApiError } from '@/utils/api';
import { suiteKeys } from '@/utils/api/queryKeys';
import type { SuiteValidateResult, TestCase, TestCaseInput } from '@/utils/api/types';
import CaseEditDialog from './components/CaseEditDialog';

function invalidateSuite(queryClient: ReturnType<typeof useQueryClient>, suiteId: number) {
  void queryClient.invalidateQueries({ queryKey: suiteKeys.detail(suiteId) });
  void queryClient.invalidateQueries({ queryKey: suiteKeys.cases(suiteId) });
  void queryClient.invalidateQueries({ queryKey: suiteKeys.allLists() });
  void queryClient.invalidateQueries({ queryKey: suiteKeys.planEditor() });
}

function DriftBadge({ stale }: { stale: boolean }) {
  if (!stale) {
    return (
      <Badge variant="outline" className="border-success/40 text-success">
        导出一致
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="border-warning/40 text-warning">
      导出漂移
    </Badge>
  );
}

function ValidatePanel({ result }: { result: SuiteValidateResult }) {
  if (result.valid) {
    return (
      <div className={cn('rounded-md px-3 py-2 text-sm', ALERT_BOX.success)} data-testid="validate-ok">
        <CheckCircle2 className="mr-2 inline h-4 w-4" />
        校验通过
      </div>
    );
  }
  return (
    <div className="space-y-2" data-testid="validate-issues">
      {result.issues.map((issue, idx) => (
        <div
          key={`${issue.code}-${idx}`}
          className={cn(
            'rounded-md px-3 py-2 text-sm',
            issue.severity === 'error' ? ALERT_BOX.destructive : ALERT_BOX.warning,
          )}
        >
          <span className="font-mono text-xs">{issue.code}</span>
          {issue.testpoint ? ` · ${issue.testpoint}` : ''}: {issue.message}
        </div>
      ))}
    </div>
  );
}

export default function TestSuiteDetailPage() {
  const { suiteId: suiteIdParam } = useParams();
  const suiteId = Number(suiteIdParam);
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';

  const runtaskInputRef = useRef<HTMLInputElement>(null);
  const globalInputRef = useRef<HTMLInputElement>(null);

  const [caseDialogOpen, setCaseDialogOpen] = useState(false);
  const [editingCase, setEditingCase] = useState<TestCase | null>(null);
  const [validateResult, setValidateResult] = useState<SuiteValidateResult | null>(null);
  const [caseSearch, setCaseSearch] = useState('');

  const suiteQ = useQuery({
    queryKey: suiteKeys.detail(suiteId),
    queryFn: () => api.suites.get(suiteId),
    enabled: Number.isFinite(suiteId),
  });

  const casesQ = useQuery({
    queryKey: suiteKeys.cases(suiteId, caseSearch || null),
    queryFn: () => api.suites.listCases(suiteId, { q: caseSearch || undefined }),
    enabled: Number.isFinite(suiteId),
  });

  const suite = suiteQ.data;
  useDocumentTitle(suite ? `套件 ${suite.name}` : '套件详情');

  const caseMutation = useMutation({
    mutationFn: (payload: { mode: 'create' | 'update'; caseId?: number; data: TestCaseInput }) => {
      if (payload.mode === 'create') return api.suites.createCase(suiteId, payload.data);
      return api.suites.updateCase(payload.caseId!, payload.data);
    },
    onSuccess: () => {
      invalidateSuite(queryClient, suiteId);
      setCaseDialogOpen(false);
      setEditingCase(null);
      toast.success('用例已保存');
    },
    onError: (err: unknown) => toast.error(`保存失败: ${toApiError(err).message}`),
  });

  const deleteCaseMutation = useMutation({
    mutationFn: (caseId: number) => api.suites.deleteCase(caseId),
    onSuccess: () => {
      invalidateSuite(queryClient, suiteId);
      toast.success('用例已删除');
    },
    onError: (err: unknown) => toast.error(`删除失败: ${toApiError(err).message}`),
  });

  const importMutation = useMutation({
    mutationFn: ({ runtask, global }: { runtask: File; global?: File | null }) =>
      api.suites.import(suiteId, runtask, global),
    onSuccess: () => {
      invalidateSuite(queryClient, suiteId);
      toast.success('导入成功');
    },
    onError: (err: unknown) => toast.error(`导入失败: ${toApiError(err).message}`),
  });

  const validateMutation = useMutation({
    mutationFn: () => api.suites.validate(suiteId),
    onSuccess: (result) => {
      setValidateResult(result);
      toast.success(result.valid ? '校验通过' : `发现 ${result.issues.length} 项问题`);
    },
    onError: (err: unknown) => toast.error(`校验失败: ${toApiError(err).message}`),
  });

  const exportToolMutation = useMutation({
    mutationFn: () => api.suites.exportToToolDir(suiteId),
    onSuccess: (result) => {
      invalidateSuite(queryClient, suiteId);
      toast.success(`已导出到 ${result.export_dir}`);
    },
    onError: (err: unknown) => toast.error(`导出失败: ${toApiError(err).message}`),
  });

  const deactivateMutation = useMutation({
    mutationFn: () => api.suites.remove(suiteId),
    onSuccess: () => {
      invalidateSuite(queryClient, suiteId);
      toast.success('套件已停用');
      navigate('/test-suites');
    },
    onError: (err: unknown) => toast.error(`停用失败: ${toApiError(err).message}`),
  });

  const handleImport = (runtask: File, global?: File | null) => {
    importMutation.mutate({ runtask, global });
  };

  const handleExport = async () => {
    try {
      const stale = await api.suites.downloadExport(suiteId, `${suite?.name ?? 'suite'}.xml`);
      toast.success(stale ? '已下载（库内容已漂移，建议重导）' : '已下载 runtask.xml');
    } catch (err: unknown) {
      toast.error(`下载失败: ${toApiError(err).message}`);
    }
  };

  const handleExportGlobal = async () => {
    try {
      const blob = await api.suites.exportGlobal(suiteId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'UiAutomatorTestData.xml';
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success('已下载 UiAutomatorTestData.xml');
    } catch (err: unknown) {
      toast.error(`下载失败: ${toApiError(err).message}`);
    }
  };

  if (!Number.isFinite(suiteId)) {
    return <ErrorState title="无效的套件 ID" />;
  }

  if (suiteQ.isLoading) {
    return (
      <PageContainer>
        <PageSkeleton>
          <PageSkeleton.Cards count={2} />
        </PageSkeleton>
      </PageContainer>
    );
  }
  if (suiteQ.isError || !suite) {
    return (
      <PageContainer>
        <ErrorState
          title="加载失败"
          description={suiteQ.error ? toApiError(suiteQ.error).message : '套件不存在'}
          onRetry={() => void suiteQ.refetch()}
        />
      </PageContainer>
    );
  }

  const diskDrift = suite.exported_content_sha256
    && suite.content_sha256
    && suite.exported_content_sha256 !== suite.content_sha256;

  return (
    <PageContainer width="content">
      <div className="mb-4">
        <Link
          to="/test-suites"
          className={cn('inline-flex items-center gap-1 text-sm', TEXT.subtitle, 'hover:text-foreground')}
        >
          <ArrowLeft className="h-4 w-4" />
          返回套件列表
        </Link>
      </div>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold" data-testid="suite-detail-title">{suite.name}</h1>
          {suite.display_name && <p className={TEXT.subtitle}>{suite.display_name}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <DriftBadge stale={suite.export_stale} />
            {diskDrift && (
              <Badge variant="outline" className="border-destructive/40 text-destructive">
                磁盘导出物漂移
              </Badge>
            )}
            {suite.project_key && (
              <Badge variant="secondary">项目 {suite.project_key}</Badge>
            )}
          </div>
        </div>
        {isAdmin && (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              data-testid="suite-validate-btn"
              onClick={() => validateMutation.mutate()}
              disabled={validateMutation.isPending}
            >
              <CheckCircle2 className="mr-2 h-4 w-4" />
              校验
            </Button>
            <Button size="sm" variant="outline" onClick={() => void handleExport()}>
              <Download className="mr-2 h-4 w-4" />
              导出 XML
            </Button>
            <Button size="sm" variant="outline" onClick={() => void handleExportGlobal()}>
              <FileUp className="mr-2 h-4 w-4" />
              导出 Global
            </Button>
            <Button
              size="sm"
              variant="outline"
              data-testid="suite-export-tool-btn"
              onClick={() => exportToolMutation.mutate()}
              disabled={exportToolMutation.isPending}
            >
              <FolderOutput className="mr-2 h-4 w-4" />
              导出到工具目录
            </Button>
            <Button
              size="sm"
              variant="outline"
              data-testid="suite-import-btn"
              onClick={() => runtaskInputRef.current?.click()}
              disabled={importMutation.isPending}
            >
              <Upload className="mr-2 h-4 w-4" />
              导入 runtask
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setEditingCase(null);
                setCaseDialogOpen(true);
              }}
            >
              <Plus className="mr-2 h-4 w-4" />
              新增用例
            </Button>
            <Button
              size="sm"
              variant="outline"
              className={STATUS_CHIP.destructive}
              onClick={() => {
                if (window.confirm(`停用套件「${suite.name}」？已有 Plan 绑定将无法继续派发。`)) {
                  deactivateMutation.mutate();
                }
              }}
              disabled={deactivateMutation.isPending}
            >
              <Trash2 className="mr-2 h-4 w-4" />
              停用
            </Button>
          </div>
        )}
      </div>

      <input
        ref={runtaskInputRef}
        type="file"
        accept=".xml"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (!file) return;
          const global = globalInputRef.current?.files?.[0] ?? null;
          handleImport(file, global);
        }}
      />
      <input ref={globalInputRef} type="file" accept=".xml" className="hidden" />

      <div className="mb-6 grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">套件信息</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p><span className={TEXT.subtitle}>用例：</span>{suite.enabled_case_count}/{suite.case_count} 启用</p>
            <p><span className={TEXT.subtitle}>导出目录：</span>{suite.export_dir ?? '—'}</p>
            <p className="break-all font-mono text-xs">
              <span className={TEXT.subtitle}>库指纹：</span>{suite.content_sha256 ?? '—'}
            </p>
            <p className="break-all font-mono text-xs">
              <span className={TEXT.subtitle}>导出指纹：</span>{suite.exported_content_sha256 ?? '未导出'}
            </p>
          </CardContent>
        </Card>
        {validateResult && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">校验结果</CardTitle>
            </CardHeader>
            <CardContent>
              <ValidatePanel result={validateResult} />
            </CardContent>
          </Card>
        )}
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
          <CardTitle className="text-base">用例列表</CardTitle>
          <input
            data-testid="case-search"
            className={cn(FORM.input, 'max-w-xs')}
            placeholder="搜索用例名…"
            value={caseSearch}
            onChange={(e) => setCaseSearch(e.target.value)}
          />
        </CardHeader>
        <CardContent>
          {casesQ.isLoading && (
            <PageSkeleton>
              <PageSkeleton.Block size="lg" />
            </PageSkeleton>
          )}
          {casesQ.isError && (
            <ErrorState
              title="用例加载失败"
              description={toApiError(casesQ.error).message}
              onRetry={() => void casesQ.refetch()}
            />
          )}
          {!casesQ.isLoading && !casesQ.isError && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="py-2 pr-3">#</th>
                    <th className="py-2 pr-3">名称</th>
                    <th className="py-2 pr-3">次数</th>
                    <th className="py-2 pr-3">状态</th>
                    {isAdmin && <th className="py-2">操作</th>}
                  </tr>
                </thead>
                <tbody>
                  {(casesQ.data ?? []).map((testCase) => (
                    <tr key={testCase.id} className="border-b border-border/60" data-testid={`case-row-${testCase.id}`}>
                      <td className="py-2 pr-3 font-mono text-xs">{testCase.ordinal}</td>
                      <td className="py-2 pr-3">{testCase.name}</td>
                      <td className="py-2 pr-3">{testCase.times}</td>
                      <td className="py-2 pr-3">
                        {testCase.enabled ? (
                          <Badge variant="outline" className="border-success/40 text-success">启用</Badge>
                        ) : (
                          <Badge variant="outline">禁用</Badge>
                        )}
                      </td>
                      {isAdmin && (
                        <td className="py-2">
                          <div className="flex gap-1">
                            <Button
                              size="sm"
                              variant="ghost"
                              aria-label="编辑用例"
                              onClick={() => {
                                setEditingCase(testCase);
                                setCaseDialogOpen(true);
                              }}
                            >
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              aria-label="删除用例"
                              onClick={() => {
                                if (window.confirm(`删除用例「${testCase.name}」？`)) {
                                  deleteCaseMutation.mutate(testCase.id);
                                }
                              }}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
              {(casesQ.data ?? []).length === 0 && (
                <p className={cn('py-6 text-center text-sm', TEXT.subtitle)}>暂无用例</p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <CaseEditDialog
        isOpen={caseDialogOpen}
        isSubmitting={caseMutation.isPending}
        initial={editingCase}
        onClose={() => {
          setCaseDialogOpen(false);
          setEditingCase(null);
        }}
        onSubmit={(payload) => {
          if (editingCase) {
            caseMutation.mutate({ mode: 'update', caseId: editingCase.id, data: payload });
          } else {
            caseMutation.mutate({ mode: 'create', data: payload });
          }
        }}
      />
    </PageContainer>
  );
}
