import React from 'react';

interface RunLogsTabProps {
  runId: number;
}

export const RunLogsTab: React.FC<RunLogsTabProps> = () => {
  return (
    <div className="p-4 text-sm text-muted-foreground">
      日志（待接入 API）
    </div>
  );
};

export default RunLogsTab;
