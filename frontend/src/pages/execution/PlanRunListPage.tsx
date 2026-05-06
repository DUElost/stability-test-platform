import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { api } from '@/utils/api';
import { Play, Clock, CheckCircle, XCircle, AlertTriangle } from 'lucide-react';

const STATUS_CONFIG: Record<string, { color: string; icon: React.ElementType; label: string }> = {
  RUNNING: { color: 'bg-blue-100 text-blue-700', icon: Play, label: '运行中' },
  SUCCESS: { color: 'bg-green-100 text-green-700', icon: CheckCircle, label: '成功' },
  PARTIAL_SUCCESS: { color: 'bg-yellow-100 text-yellow-700', icon: AlertTriangle, label: '部分成功' },
  FAILED: { color: 'bg-red-100 text-red-700', icon: XCircle, label: '失败' },
  DEGRADED: { color: 'bg-orange-100 text-orange-700', icon: AlertTriangle, label: '降级' },
};

export default function PlanRunListPage() {
  const navigate = useNavigate();

  const { data: runs, isLoading } = useQuery({
    queryKey: ['plan-runs-list'],
    queryFn: () => api.planRuns.list(0, 50),
    refetchInterval: 15_000,
  });

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Plan 执行记录</h1>
        <p className="text-gray-500 mt-1">查看所有 PlanRun 历史记录</p>
      </div>

      {isLoading ? (
        <div className="space-y-3"><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /></div>
      ) : !runs || runs.length === 0 ? (
        <Card><CardContent className="py-12 text-center text-gray-400">
          <Clock className="w-10 h-10 mx-auto mb-3 text-gray-300" />
          <p className="text-sm">暂无执行记录</p>
        </CardContent></Card>
      ) : (
        <div className="space-y-2">
          {runs.map(run => {
            const cfg = STATUS_CONFIG[run.status] || STATUS_CONFIG.RUNNING;
            const Icon = cfg.icon;
            return (
              <Card key={run.id} className="hover:shadow-md transition-shadow cursor-pointer"
                onClick={() => navigate(`/execution/plan-runs/${run.id}`)}>
                <CardContent className="py-3 flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <span className="font-mono text-sm text-gray-500">#{run.id}</span>
                    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${cfg.color}`}>
                      <Icon className="w-3 h-3" />{cfg.label}
                    </span>
                    <span className="text-sm text-gray-700">Plan #{run.plan_id}</span>
                    <span className="text-xs text-gray-400">{run.run_type}</span>
                  </div>
                  <div className="flex items-center gap-4 text-xs text-gray-400">
                    {run.triggered_by && <span>{run.triggered_by}</span>}
                    <span>{new Date(run.started_at).toLocaleString()}</span>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
