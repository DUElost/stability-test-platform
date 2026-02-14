import { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  Server,
  Cpu,
  HardDrive,
  MemoryStick,
  Activity,
  Wifi,
  WifiOff,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  Rocket,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

export interface HostResources {
  cpu_load: number;
  cpu_cores?: number;
  ram_usage: number;
  ram_total_gb?: number;
  disk_usage: number;
  disk_total_gb?: number;
  temperature?: number;
  uptime_seconds?: number;
}

export interface MountStatus {
  path: string;
  mounted: boolean;
  available_gb?: number;
  total_gb?: number;
}

export interface HostResourceCardProps {
  id: number;
  name: string;
  ip: string;
  status: 'ONLINE' | 'OFFLINE' | 'DEGRADED';
  last_heartbeat?: string;
  resources?: HostResources;
  mount_status?: MountStatus[];
  device_count?: number;
  active_tasks?: number;
  className?: string;
  onDeploy?: (hostId: number) => void;
  isDeploying?: boolean;
}

const statusConfig = {
  ONLINE: {
    label: 'Online',
    variant: 'success' as const,
    icon: CheckCircle2,
    bgColor: 'bg-success/10',
    borderColor: 'border-success/30',
  },
  OFFLINE: {
    label: 'Offline',
    variant: 'secondary' as const,
    icon: WifiOff,
    bgColor: 'bg-muted',
    borderColor: 'border-muted',
  },
  DEGRADED: {
    label: 'Degraded',
    variant: 'warning' as const,
    icon: AlertTriangle,
    bgColor: 'bg-warning/10',
    borderColor: 'border-warning/30',
  },
};

function formatDuration(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatBytes(gb: number): string {
  if (gb >= 1024) return `${(gb / 1024).toFixed(1)} TB`;
  return `${gb.toFixed(1)} GB`;
}

function getResourceColor(percentage: number): string {
  if (percentage >= 90) return 'text-destructive';
  if (percentage >= 70) return 'text-warning';
  return 'text-success';
}

function getProgressColor(percentage: number): string {
  if (percentage >= 90) return 'bg-destructive';
  if (percentage >= 70) return 'bg-warning';
  return 'bg-success';
}

export function HostResourceCard({
  id,
  name,
  ip,
  status,
  last_heartbeat,
  resources,
  mount_status,
  device_count = 0,
  active_tasks = 0,
  className,
  onDeploy,
  isDeploying,
}: HostResourceCardProps) {
  const config = statusConfig[status];
  const StatusIcon = config.icon;

  const isStale = useMemo(() => {
    if (!last_heartbeat) return true;
    const last = new Date(last_heartbeat).getTime();
    const now = Date.now();
    return now - last > 5 * 60 * 1000; // 5 minutes
  }, [last_heartbeat]);

  const hasResourceWarning = useMemo(() => {
    if (!resources) return false;
    return (
      resources.cpu_load > 80 ||
      resources.ram_usage > 85 ||
      resources.disk_usage > 90 ||
      (resources.temperature && resources.temperature > 80)
    );
  }, [resources]);

  const hasMountIssues = useMemo(() => {
    if (!mount_status) return false;
    return mount_status.some((m) => !m.mounted);
  }, [mount_status]);

  return (
    <TooltipProvider>
      <Card
        className={cn(
          'transition-all duration-200 hover:shadow-md',
          config.bgColor,
          config.borderColor,
          'border-l-4',
          className
        )}
      >
        <CardHeader className="p-4 pb-2">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-3">
              <div
                className={cn(
                  'p-2 rounded-lg',
                  status === 'ONLINE'
                    ? 'bg-success/20'
                    : status === 'DEGRADED'
                    ? 'bg-warning/20'
                    : 'bg-muted'
                )}
              >
                <Server
                  className={cn(
                    'h-5 w-5',
                    status === 'ONLINE'
                      ? 'text-success'
                      : status === 'DEGRADED'
                      ? 'text-warning'
                      : 'text-muted-foreground'
                  )}
                />
              </div>
              <div>
                <CardTitle className="text-base font-semibold">{name}</CardTitle>
                <p className="text-xs text-muted-foreground font-mono">{ip}</p>
              </div>
            </div>
            <div className="flex flex-col items-end gap-1">
              <Badge variant={config.variant} className="flex items-center gap-1">
                <StatusIcon className="h-3 w-3" />
                {config.label}
              </Badge>
              {isStale && status === 'ONLINE' && (
                <Badge variant="outline" className="text-xs text-warning">
                  Stale
                </Badge>
              )}
              {onDeploy && (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs gap-1"
                  onClick={() => onDeploy(id)}
                  disabled={isDeploying}
                >
                  <Rocket className="h-3 w-3" />
                  {isDeploying ? 'Deploying...' : 'Deploy'}
                </Button>
              )}
            </div>
          </div>
        </CardHeader>

        <CardContent className="p-4 pt-2 space-y-4">
          {/* Quick Stats */}
          <div className="grid grid-cols-2 gap-2">
            <div className="flex items-center gap-2 p-2 bg-background/50 rounded-md">
              <Database className="h-4 w-4 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">Devices</p>
                <p className="text-sm font-semibold">{device_count}</p>
              </div>
            </div>
            <div className="flex items-center gap-2 p-2 bg-background/50 rounded-md">
              <Activity className="h-4 w-4 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">Active Tasks</p>
                <p className="text-sm font-semibold">{active_tasks}</p>
              </div>
            </div>
          </div>

          {/* Resource Usage */}
          {resources && status === 'ONLINE' && (
            <div className="space-y-3">
              {/* CPU */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-1.5">
                        <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="text-muted-foreground">CPU</span>
                        {resources.cpu_cores && (
                          <span className="text-muted-foreground/60">
                            ({resources.cpu_cores} cores)
                          </span>
                        )}
                      </div>
                      <span
                        className={cn(
                          'font-mono font-medium',
                          getResourceColor(resources.cpu_load)
                        )}
                      >
                        {resources.cpu_load.toFixed(1)}%
                      </span>
                    </div>
                    <Progress
                      value={resources.cpu_load}
                      className="h-1.5"
                      indicatorClassName={getProgressColor(resources.cpu_load)}
                    />
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  <p>CPU Load: {resources.cpu_load.toFixed(1)}%</p>
                </TooltipContent>
              </Tooltip>

              {/* RAM */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-1.5">
                        <MemoryStick className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="text-muted-foreground">RAM</span>
                        {resources.ram_total_gb && (
                          <span className="text-muted-foreground/60">
                            ({formatBytes(resources.ram_total_gb)})
                          </span>
                        )}
                      </div>
                      <span
                        className={cn(
                          'font-mono font-medium',
                          getResourceColor(resources.ram_usage)
                        )}
                      >
                        {resources.ram_usage.toFixed(1)}%
                      </span>
                    </div>
                    <Progress
                      value={resources.ram_usage}
                      className="h-1.5"
                      indicatorClassName={getProgressColor(resources.ram_usage)}
                    />
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  <p>RAM Usage: {resources.ram_usage.toFixed(1)}%</p>
                </TooltipContent>
              </Tooltip>

              {/* Disk */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-1.5">
                        <HardDrive className="h-3.5 w-3.5 text-muted-foreground" />
                        <span className="text-muted-foreground">Disk</span>
                        {resources.disk_total_gb && (
                          <span className="text-muted-foreground/60">
                            ({formatBytes(resources.disk_total_gb)})
                          </span>
                        )}
                      </div>
                      <span
                        className={cn(
                          'font-mono font-medium',
                          getResourceColor(resources.disk_usage)
                        )}
                      >
                        {resources.disk_usage.toFixed(1)}%
                      </span>
                    </div>
                    <Progress
                      value={resources.disk_usage}
                      className="h-1.5"
                      indicatorClassName={getProgressColor(resources.disk_usage)}
                    />
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Disk Usage: {resources.disk_usage.toFixed(1)}%</p>
                </TooltipContent>
              </Tooltip>

              {/* Temperature */}
              {resources.temperature !== undefined && (
                <div className="flex items-center justify-between text-xs p-2 bg-background/50 rounded-md">
                  <span className="text-muted-foreground">Temperature</span>
                  <span
                    className={cn(
                      'font-mono font-medium',
                      resources.temperature > 80
                        ? 'text-destructive'
                        : resources.temperature > 60
                        ? 'text-warning'
                        : 'text-success'
                    )}
                  >
                    {resources.temperature.toFixed(1)}°C
                  </span>
                </div>
              )}

              {/* Uptime */}
              {resources.uptime_seconds !== undefined && (
                <div className="flex items-center justify-between text-xs p-2 bg-background/50 rounded-md">
                  <div className="flex items-center gap-1.5">
                    <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="text-muted-foreground">Uptime</span>
                  </div>
                  <span className="font-mono">{formatDuration(resources.uptime_seconds)}</span>
                </div>
              )}
            </div>
          )}

          {/* Mount Status */}
          {mount_status && mount_status.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">Mount Points</p>
              <div className="space-y-1">
                {mount_status.map((mount) => (
                  <Tooltip key={mount.path}>
                    <TooltipTrigger asChild>
                      <div
                        className={cn(
                          'flex items-center justify-between p-2 rounded-md text-xs',
                          mount.mounted
                            ? 'bg-success/10 border border-success/20'
                            : 'bg-destructive/10 border border-destructive/20'
                        )}
                      >
                        <div className="flex items-center gap-1.5">
                          {mount.mounted ? (
                            <CheckCircle2 className="h-3 w-3 text-success" />
                          ) : (
                            <AlertTriangle className="h-3 w-3 text-destructive" />
                          )}
                          <span className="font-mono truncate max-w-[120px]">{mount.path}</span>
                        </div>
                        {mount.available_gb !== undefined && (
                          <span className="text-muted-foreground">
                            {formatBytes(mount.available_gb)} free
                          </span>
                        )}
                      </div>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>
                        {mount.mounted ? 'Mounted' : 'Not Mounted'}: {mount.path}
                      </p>
                      {mount.total_gb && (
                        <p>
                          {formatBytes(mount.available_gb || 0)} / {formatBytes(mount.total_gb)}{' '}
                          available
                        </p>
                      )}
                    </TooltipContent>
                  </Tooltip>
                ))}
              </div>
            </div>
          )}

          {/* Warnings */}
          {(hasResourceWarning || hasMountIssues) && (
            <div className="flex items-start gap-2 p-2 bg-warning/10 border border-warning/20 rounded-md">
              <AlertTriangle className="h-4 w-4 text-warning flex-shrink-0 mt-0.5" />
              <div className="text-xs text-warning">
                {hasResourceWarning && <p>High resource usage detected</p>}
                {hasMountIssues && <p>Some mount points are not available</p>}
              </div>
            </div>
          )}

          {/* Last Heartbeat */}
          {last_heartbeat && (
            <div className="flex items-center justify-between text-xs text-muted-foreground pt-2 border-t">
              <div className="flex items-center gap-1">
                <Wifi className="h-3 w-3" />
                <span>Last seen</span>
              </div>
              <span>{new Date(last_heartbeat).toLocaleString()}</span>
            </div>
          )}
        </CardContent>
      </Card>
    </TooltipProvider>
  );
}
