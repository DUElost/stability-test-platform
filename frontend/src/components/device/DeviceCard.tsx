import React from 'react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { ConnectivityBadge } from '../network/ConnectivityBadge';
import { AlertTriangle, Activity, Server } from 'lucide-react';

export interface Device {
  serial: string;
  model: string;
  status: 'idle' | 'testing' | 'offline' | 'error';
  battery_level: number;
  temperature: number;
  network_latency?: number | null;
  current_task?: string;
  host_name?: string;
  host_id?: number | null;
}

const statusConfig = {
  idle: { variant: 'success' as const, label: 'Idle', bgColor: 'bg-success/10' },
  testing: { variant: 'default' as const, label: 'Testing', bgColor: 'bg-primary/10' },
  offline: { variant: 'secondary' as const, label: 'Offline', bgColor: 'bg-muted' },
  error: { variant: 'destructive' as const, label: 'Error', bgColor: 'bg-destructive/10' },
};

export const DeviceCard: React.FC<{ device: Device; onClick?: (d: Device) => void }> = ({ device, onClick }) => {
  const config = statusConfig[device.status];

  const getNetworkStatus = (): 'online' | 'offline' | 'warning' => {
    if (device.network_latency === null || device.network_latency === undefined) {
      return 'offline';
    }
    if (device.network_latency > 200) {
      return 'warning';
    }
    return 'online';
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (onClick && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      onClick(device);
    }
  };

  return (
    <TooltipProvider>
      <Card
        onClick={() => onClick?.(device)}
        onKeyDown={handleKeyDown}
        role={onClick ? "button" : undefined}
        tabIndex={onClick ? 0 : undefined}
        aria-label={`Device ${device.model} - ${config.label}`}
        className={`cursor-pointer transition-all duration-200 hover:shadow-md hover:-translate-y-0.5 ${config.bgColor} border-l-4 ${
          device.status === 'idle' ? 'border-l-success' :
          device.status === 'testing' ? 'border-l-primary' :
          device.status === 'error' ? 'border-l-destructive' : 'border-l-muted'
        }`}
      >
        <CardHeader className="p-4 pb-2">
          <div className="flex justify-between items-start">
            <div className="min-w-0 flex-1">
              <h4 className="font-semibold text-sm truncate text-card-foreground">{device.model}</h4>
              <p className="text-xs font-mono text-muted-foreground">{device.serial}</p>
              {device.host_id && (
                <div className="mt-1.5 flex items-center gap-1">
                  <Server size={10} className="text-muted-foreground flex-shrink-0" />
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded font-medium truncate max-w-[150px] ${
                          device.host_name
                            ? 'bg-secondary text-secondary-foreground'
                            : 'bg-warning/20 text-warning'
                        }`}
                      >
                        {device.host_name || `Host #${device.host_id}`}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{device.host_name || `Host ID: ${device.host_id}`}</p>
                    </TooltipContent>
                  </Tooltip>
                </div>
              )}
            </div>
            <Badge variant={config.variant} className="text-[10px] uppercase flex-shrink-0">
              {config.label}
            </Badge>
          </div>
        </CardHeader>

        <CardContent className="p-4 pt-0">
          {device.status === 'testing' && device.current_task && (
            <div className="mb-3 bg-primary/10 px-2 py-1.5 rounded-md border border-primary/20 flex items-center gap-2">
              <Activity size={12} className="text-primary animate-pulse" />
              <span className="text-xs font-medium text-primary truncate">{device.current_task}</span>
            </div>
          )}

          {device.status !== 'offline' && (
            <div className="space-y-3 mt-3 text-xs">
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-muted/50 p-2 rounded-md">
                  <span className="text-muted-foreground block mb-1.5 text-[10px] uppercase tracking-wider">Battery</span>
                  <div className="flex items-center gap-2">
                    <div className="flex-1">
                      <Progress
                        value={device.battery_level}
                        className="h-1.5"
                      />
                    </div>
                    <span className={`font-mono font-semibold ${
                      device.battery_level < 20 ? 'text-destructive' : 'text-foreground'
                    }`}>
                      {device.battery_level}%
                    </span>
                  </div>
                </div>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className={`p-2 rounded-md ${
                      device.temperature > 45 ? 'bg-destructive/10 border border-destructive/20' : 'bg-muted/50'
                    }`}>
                      <span className="text-muted-foreground block mb-1 text-[10px] uppercase tracking-wider">Temp</span>
                      <div className="flex items-center justify-between">
                        <span className={`font-mono font-bold ${
                          device.temperature > 40 ? 'text-destructive' : 'text-foreground'
                        }`}>
                          {device.temperature}°C
                        </span>
                        {device.temperature > 45 && <AlertTriangle size={12} className="text-destructive" />}
                      </div>
                    </div>
                  </TooltipTrigger>
                  {device.temperature > 40 && (
                    <TooltipContent>
                      <p>High temperature warning: {device.temperature}°C</p>
                    </TooltipContent>
                  )}
                </Tooltip>
              </div>

              <div className="bg-muted/50 p-2 rounded-md">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground text-[10px] uppercase tracking-wider">Network</span>
                  <ConnectivityBadge
                    status={getNetworkStatus()}
                    latency={device.network_latency ?? undefined}
                  />
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </TooltipProvider>
  );
};
