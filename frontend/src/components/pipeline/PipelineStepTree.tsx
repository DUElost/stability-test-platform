import React, { useState, useEffect, useMemo } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Circle,
  CheckCircle2,
  XCircle,
  MinusCircle,
  Loader2,
} from 'lucide-react';
import type { RunStep } from '@/utils/api';

// ---------- Types ----------

export interface StepUpdateMessage {
  type: 'STEP_UPDATE';
  step_id: number;
  status: RunStep['status'];
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number | null;
  error_message?: string | null;
}

interface PhaseGroup {
  name: string;
  steps: RunStep[];
}

interface PipelineStepTreeProps {
  steps: RunStep[];
  selectedStepId: number | null;
  onStepSelect: (stepId: number) => void;
  /** Incoming WS step updates to apply reactively */
  stepUpdates?: StepUpdateMessage[];
}

// ---------- Helpers ----------

function groupByPhase(steps: RunStep[]): PhaseGroup[] {
  const map = new Map<string, RunStep[]>();
  for (const step of steps) {
    const arr = map.get(step.phase) || [];
    arr.push(step);
    map.set(step.phase, arr);
  }
  return Array.from(map.entries()).map(([name, steps]) => ({
    name,
    steps: steps.sort((a, b) => a.step_order - b.step_order),
  }));
}

function hasRunningStep(phase: PhaseGroup): boolean {
  return phase.steps.some((s) => s.status === 'RUNNING');
}

function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt) return '';
  const start = new Date(startedAt).getTime();
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  const diffMs = Math.max(0, end - start);
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainSec = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remainSec}s`;
  const hours = Math.floor(minutes / 60);
  const remainMin = minutes % 60;
  return `${hours}h ${remainMin}m`;
}

// ---------- Step status icon ----------

function StepStatusIcon({ status }: { status: RunStep['status'] }) {
  switch (status) {
    case 'RUNNING':
      return <Loader2 size={14} className="text-blue-500 animate-spin" />;
    case 'COMPLETED':
      return <CheckCircle2 size={14} className="text-green-500" />;
    case 'FAILED':
      return <XCircle size={14} className="text-red-500" />;
    case 'SKIPPED':
      return <MinusCircle size={14} className="text-slate-400" />;
    case 'CANCELED':
      return <MinusCircle size={14} className="text-slate-400" />;
    default:
      return <Circle size={14} className="text-slate-400" />;
  }
}

// ---------- Phase group ----------

function PhaseSection({
  phase,
  isExpanded,
  onToggle,
  selectedStepId,
  onStepSelect,
}: {
  phase: PhaseGroup;
  isExpanded: boolean;
  onToggle: () => void;
  selectedStepId: number | null;
  onStepSelect: (stepId: number) => void;
}) {
  const phaseHasRunning = hasRunningStep(phase);
  const phaseCompleted = phase.steps.every(
    (s) => s.status === 'COMPLETED' || s.status === 'SKIPPED',
  );
  const phaseFailed = phase.steps.some((s) => s.status === 'FAILED');

  return (
    <div className="mb-1">
      {/* Phase header */}
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm font-medium text-slate-300 hover:bg-slate-800 rounded transition-colors"
      >
        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="flex-1 text-left">{phase.name}</span>
        {phaseHasRunning && (
          <span className="text-[10px] px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded-full">
            running
          </span>
        )}
        {phaseCompleted && !phaseHasRunning && (
          <CheckCircle2 size={12} className="text-green-500" />
        )}
        {phaseFailed && !phaseHasRunning && (
          <XCircle size={12} className="text-red-500" />
        )}
      </button>

      {/* Steps */}
      {isExpanded && (
        <div className="ml-3 border-l border-slate-700">
          {phase.steps.map((step) => {
            const isSelected = step.id === selectedStepId;
            const isRunning = step.status === 'RUNNING';

            return (
              <button
                key={step.id}
                onClick={() => onStepSelect(step.id)}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors
                  ${isSelected ? 'bg-slate-700/70 text-white' : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'}
                  ${isRunning ? 'border-l-2 border-blue-500 -ml-px' : ''}
                `}
              >
                <StepStatusIcon status={step.status} />
                <span className={`flex-1 text-left truncate ${step.status === 'SKIPPED' ? 'line-through text-slate-500' : ''}`}>
                  {step.name}
                </span>
                <span className="text-[10px] text-slate-500 font-mono whitespace-nowrap">
                  {formatDuration(step.started_at, step.finished_at)}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------- Main component ----------

export function PipelineStepTree({
  steps: initialSteps,
  selectedStepId,
  onStepSelect,
  stepUpdates,
}: PipelineStepTreeProps) {
  // Merge WS updates into step state
  const [localSteps, setLocalSteps] = useState<RunStep[]>(initialSteps);

  useEffect(() => {
    setLocalSteps(initialSteps);
  }, [initialSteps]);

  // Apply incoming step updates
  useEffect(() => {
    if (!stepUpdates || stepUpdates.length === 0) return;
    setLocalSteps((prev) => {
      let updated = [...prev];
      for (const upd of stepUpdates) {
        const idx = updated.findIndex((s) => s.id === upd.step_id);
        if (idx >= 0) {
          updated[idx] = {
            ...updated[idx],
            status: upd.status,
            ...(upd.started_at !== undefined && { started_at: upd.started_at }),
            ...(upd.finished_at !== undefined && { finished_at: upd.finished_at }),
            ...(upd.exit_code !== undefined && { exit_code: upd.exit_code }),
            ...(upd.error_message !== undefined && { error_message: upd.error_message }),
          };
        }
      }
      return updated;
    });
  }, [stepUpdates]);

  const phases = useMemo(() => groupByPhase(localSteps), [localSteps]);

  // Expand phases: auto-expand the phase with a RUNNING step
  const [expandedPhases, setExpandedPhases] = useState<Set<string>>(new Set());

  useEffect(() => {
    const newExpanded = new Set<string>();
    for (const phase of phases) {
      if (hasRunningStep(phase)) {
        newExpanded.add(phase.name);
      }
    }
    // If nothing is running, expand the first non-completed phase
    if (newExpanded.size === 0 && phases.length > 0) {
      const first = phases.find(
        (p) => !p.steps.every((s) => s.status === 'COMPLETED' || s.status === 'SKIPPED'),
      );
      if (first) newExpanded.add(first.name);
      else newExpanded.add(phases[0].name); // All done, expand first
    }
    setExpandedPhases(newExpanded);
  }, [localSteps]); // Re-evaluate when steps change

  // Live duration tick for running steps
  const [, setTick] = useState(0);
  const hasRunning = localSteps.some((s) => s.status === 'RUNNING');
  useEffect(() => {
    if (!hasRunning) return;
    const timer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(timer);
  }, [hasRunning]);

  const togglePhase = (name: string) => {
    setExpandedPhases((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  if (localSteps.length === 0) {
    return (
      <div className="p-4 text-sm text-slate-500 text-center">
        No pipeline steps
      </div>
    );
  }

  return (
    <div className="py-2">
      {phases.map((phase) => (
        <PhaseSection
          key={phase.name}
          phase={phase}
          isExpanded={expandedPhases.has(phase.name)}
          onToggle={() => togglePhase(phase.name)}
          selectedStepId={selectedStepId}
          onStepSelect={onStepSelect}
        />
      ))}
    </div>
  );
}

export default PipelineStepTree;
