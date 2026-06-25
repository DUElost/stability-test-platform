import React from 'react';
import { BORDER, ELEVATION, EVENT_SEVERITY_DOT, SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { Host } from './HostCard';

interface NetworkTopologyProps {
  centralServer: string;
  hosts: Host[];
}

const HOST_STATUS_DOT: Record<Host['status'], string> = {
  online: EVENT_SEVERITY_DOT.ok,
  offline: EVENT_SEVERITY_DOT.err,
  warning: EVENT_SEVERITY_DOT.warn,
};

export const NetworkTopology: React.FC<NetworkTopologyProps> = ({ centralServer, hosts }) => {
  return (
    <div
      className={cn(
        'flex flex-col items-center p-8 rounded-xl border border-dashed',
        SURFACE.subtle,
        BORDER.default,
      )}
    >
      <div
        className={cn(
          'relative z-10 bg-primary text-primary-foreground p-4 rounded-full w-32 h-32',
          'flex items-center justify-center text-center mb-12',
          ELEVATION.lg,
        )}
      >
        <div>
          <div className="font-bold text-lg">中心服务器</div>
          <div className="text-xs opacity-80">{centralServer}</div>
        </div>
      </div>

      <div className="relative w-full flex justify-center space-x-8">
        <div className="absolute top-0 left-0 w-full -mt-12 flex justify-center pointer-events-none">
          <div className={cn('w-[80%] h-12 border-t-2 border-l-2 border-r-2 rounded-t-3xl', BORDER.default)} />
        </div>

        {hosts.map((host) => (
          <div key={host.ip} className="flex flex-col items-center z-10 mt-4">
            <div className={cn('w-3 h-3 rounded-full mb-2', HOST_STATUS_DOT[host.status])} />
            <div className={cn('p-3 rounded text-center w-24', SURFACE.elevated, BORDER.default, ELEVATION.sm)}>
              <div className={cn('text-xs font-mono font-medium', TEXT.body)}>{host.ip}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
