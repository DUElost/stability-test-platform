import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ExecuteCommandBar, type ExecuteCommandBarSummary } from './ExecuteCommandBar';

const baseSummary: ExecuteCommandBarSummary = {
  selectedCount: 0,
  hostCount: 0,
  versionCount: 0,
  versionConsistent: true,
  readyCount: 0,
  blockedCount: 0,
  showDeviceMeta: true,
};

function renderBar(over: Partial<ExecuteCommandBarSummary> = {}) {
  return render(
    <ExecuteCommandBar
      phase="select"
      onPhaseChange={vi.fn()}
      summary={{ ...baseSummary, ...over }}
      primaryLabel="预览发起"
      onPrimary={vi.fn()}
    />,
  );
}

describe('ExecuteCommandBar 版本槽（#2385）', () => {
  // 空集被判为「一致 ✓」：判据本身（versionConsistent）在 0 台时恒真，但这一步的
  // 真实状态是「还没选机」——与同条「预检 —」一样按缺省态渲染。
  it('renders a neutral 版本 — slot instead of green 一致 ✓ when nothing is selected', () => {
    renderBar({ selectedCount: 0, versionCount: 0, versionConsistent: true });

    const chip = screen.getByText('版本 —');
    expect(chip.className).toMatch(/bg-muted/);
    expect(chip.className).not.toMatch(/text-success/);
    expect(screen.queryByText(/一致/)).not.toBeInTheDocument();
  });

  it('still reports 版本一致/冲突 once devices are selected', () => {
    const { unmount } = renderBar({
      selectedCount: 2,
      hostCount: 1,
      versionCount: 2,
      versionConsistent: true,
    });
    expect(screen.getByText('2 版本 · 一致 ✓')).toBeInTheDocument();
    unmount();

    renderBar({
      selectedCount: 2,
      hostCount: 1,
      versionCount: 2,
      versionConsistent: false,
    });
    expect(screen.getByText('2 版本 · 冲突')).toBeInTheDocument();
  });
});
