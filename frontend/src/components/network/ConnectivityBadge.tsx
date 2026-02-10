import React from 'react';

interface ConnectivityBadgeProps {
  status: 'online' | 'offline' | 'warning';
  latency?: number;
}

export const ConnectivityBadge: React.FC<ConnectivityBadgeProps> = ({ status, latency }) => {
  const colorMap = {
    online: 'bg-green-500',
    offline: 'bg-red-500',
    warning: 'bg-yellow-500',
  };

  const textColorMap = {
    online: 'text-green-700 bg-green-100',
    offline: 'text-red-700 bg-red-100',
    warning: 'text-yellow-700 bg-yellow-100',
  };

  return (
    <div className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${textColorMap[status]}`}>
      <span className={`w-2 h-2 mr-1.5 rounded-full ${colorMap[status]}`} />
      <span className="capitalize">{status}</span>
      {latency !== undefined && (
        <span className="ml-1 opacity-75">({latency}ms)</span>
      )}
    </div>
  );
};
