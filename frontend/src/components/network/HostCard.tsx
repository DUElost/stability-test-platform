import React from 'react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { ConnectivityBadge } from './ConnectivityBadge';
import { Smartphone, HardDrive } from 'lucide-react';

export interface Host {
  ip: string;
  status: 'online' | 'offline' | 'warning';
  cpu_load: number;
  ram_usage: number;
  disk_usage: number;
  mount_status: boolean;
  device_count?: number;
}

interface ResourceBarProps {
  label: string;
  value: number;
  warningThreshold?: number;
  criticalThreshold?: number;
}

const ResourceBar: React.FC<ResourceBarProps> = ({ label, value, warningThreshold = 70, criticalThreshold = 85 }) => {
  const getColorClass = (val: number) => {
    if (val >= criticalThreshold) return 'text-destructive';
    if (val >= warningThreshold) return 'text-warning';
    return 'text-foreground';
  };

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground uppercase tracking-wider">{label}</span>
        <span className={`font-mono font-semibold ${getColorClass(value)}`}>{value}%</span>
      </div>
      <Progress
        value={value}
        className={`h-1.5 ${value >= criticalThreshold ? '[&>div]:bg-destructive' : value >= warningThreshold ? '[&>div]:bg-warning' : ''}`}
      />
    </div>
  );
};

export const HostCard: React.FC<{ host: Host }> = ({ host }) => {
  return (
    <TooltipProvider>
      <Card className="transition-all duration-200 hover:shadow-md">
        <CardHeader className="p-4 pb-3">
          <div className="flex justify-between items-start">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-semibold text-card-foreground">{host.ip}</h3>
                {typeof host.device_count === 'number' && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge variant="secondary" className="text-[10px] gap-1 px-2">
                        <Smartphone size={10} />
                        {host.device_count}
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{host.device_count} device{host.device_count !== 1 ? 's' : ''} connected</p>
                    </TooltipContent>
                  </Tooltip>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">Host Node</p>
            </div>
            <ConnectivityBadge status={host.status} />
          </div>
        </CardHeader>

        <CardContent className="p-4 pt-0 space-y-4">
          <div className="space-y-3">
            <ResourceBar
              label="CPU Load"
              value={host.cpu_load}
            />
            <ResourceBar
              label="RAM Usage"
              value={host.ram_usage}
            />
            <ResourceBar
              label="Disk Usage"
              value={host.disk_usage}
            />
          </div>

          <div className="pt-3 border-t border-border flex items-center justify-between">
            <div className="flex items-center gap-2 text-muted-foreground">
              <HardDrive size={14} />
              <span className="text-xs">Storage Mount</span>
            </div>
            <Badge
              variant={host.mount_status ? 'success' : 'destructive'}
              className="text-[10px] uppercase"
            >
              {host.mount_status ? 'Mounted' : 'Unmounted'}
            </Badge>
          </div>
        </CardContent>
      </Card>
    </TooltipProvider>
  );
};
