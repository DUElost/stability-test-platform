import React from 'react';
import { Host } from './HostCard';

interface NetworkTopologyProps {
  centralServer: string;
  hosts: Host[];
}

export const NetworkTopology: React.FC<NetworkTopologyProps> = ({ centralServer, hosts }) => {
  return (
    <div className="flex flex-col items-center p-8 bg-slate-50 rounded-xl border border-dashed border-slate-300">
      <div className="relative z-10 bg-indigo-600 text-white p-4 rounded-full shadow-lg w-32 h-32 flex items-center justify-center text-center mb-12">
        <div>
          <div className="font-bold text-lg">Server</div>
          <div className="text-xs opacity-80">{centralServer}</div>
        </div>
      </div>

      <div className="relative w-full flex justify-center space-x-8">
        <div className="absolute top-0 left-0 w-full -mt-12 flex justify-center pointer-events-none">
          <div className="w-[80%] h-12 border-t-2 border-l-2 border-r-2 border-slate-300 rounded-t-3xl"></div>
        </div>

        {hosts.map((host) => (
          <div key={host.ip} className="flex flex-col items-center z-10 mt-4">
            <div className={`w-3 h-3 rounded-full mb-2 ${host.status === 'online' ? 'bg-green-500' : 'bg-red-500'}`}></div>
            <div className="bg-white p-3 rounded shadow border border-slate-200 text-center w-24">
              <div className="text-xs font-mono font-medium text-slate-700">{host.ip}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
