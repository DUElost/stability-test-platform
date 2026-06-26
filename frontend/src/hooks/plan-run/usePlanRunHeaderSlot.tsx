import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, PanelLeft, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import PlanRunTabs from '@/components/plan-run/PlanRunTabs';
import { useHeaderSlot } from '@/contexts/HeaderSlotContext';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatTimeFromMs } from '@/utils/format';

interface Options {
  runId: number;
  dataUpdatedAt: number;
  isAnyFetching: boolean;
  refreshAll: () => void;
  leftPanelOpen: boolean;
  onToggleLeftPanel: () => void;
}

export function usePlanRunHeaderSlot({
  runId,
  dataUpdatedAt,
  isAnyFetching,
  refreshAll,
  leftPanelOpen,
  onToggleLeftPanel,
}: Options) {
  const navigate = useNavigate();
  const { setHeaderSlot, setFullBleed } = useHeaderSlot();

  useEffect(() => {
    setFullBleed(true);
    setHeaderSlot(
      <div className="flex w-full items-center gap-3 min-w-0">
        <Button
          variant="ghost"
          size="sm"
          data-testid="plan-run-left-panel-toggle"
          onClick={onToggleLeftPanel}
          aria-label="切换状态面板"
          aria-expanded={leftPanelOpen}
          className="-ml-1 px-1.5 text-muted-foreground lg:hidden"
        >
          <PanelLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate('/execution/plan-runs')}
          className="-ml-2 text-xs text-muted-foreground"
        >
          <ArrowLeft className="mr-1 h-3.5 w-3.5" /> 返回执行列表
        </Button>
        <PlanRunTabs runId={runId} active="overview" />
        <div className="ml-auto flex items-center gap-2">
          <span className={cn('hidden text-[11px] sm:inline', TEXT.caption)}>
            最后更新 {dataUpdatedAt ? formatTimeFromMs(dataUpdatedAt) : '—'}
          </span>
          <Button
            variant="ghost"
            size="sm"
            data-testid="plan-run-refresh-btn"
            onClick={refreshAll}
            disabled={isAnyFetching}
            className="text-xs text-muted-foreground"
          >
            <RefreshCw
              className={`mr-1 h-3.5 w-3.5 ${isAnyFetching ? 'animate-spin' : ''}`}
            />
            刷新
          </Button>
        </div>
      </div>,
    );
    return () => {
      setHeaderSlot(null);
      setFullBleed(false);
    };
  }, [
    runId,
    navigate,
    setHeaderSlot,
    setFullBleed,
    dataUpdatedAt,
    isAnyFetching,
    refreshAll,
    leftPanelOpen,
    onToggleLeftPanel,
  ]);
}
