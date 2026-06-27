import React from 'react';

interface RunArtifactsTabProps {
  runId: number;
}

export const RunArtifactsTab: React.FC<RunArtifactsTabProps> = () => {
  return (
    <div className="p-4 text-sm text-muted-foreground">
      产物列表（待接入 API）
    </div>
  );
};

export default RunArtifactsTab;
