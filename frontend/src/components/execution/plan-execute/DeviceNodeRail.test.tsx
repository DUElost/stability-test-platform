import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DeviceNodeRail, type DeviceNodeSummary } from './DeviceNodeRail';

function node(partial: Partial<DeviceNodeSummary>): DeviceNodeSummary {
  return {
    id: 'h1',
    label: 'node-a',
    total: 2,
    selected: 0,
    available: 2,
    online: true,
    busy: 0,
    healthStatus: null,
    healthReasons: [],
    ...partial,
  };
}

function renderRail(nodes: DeviceNodeSummary[]) {
  return render(
    <DeviceNodeRail
      nodes={nodes}
      selectedHostId="h1"
      onSelectHost={vi.fn()}
      search=""
      onSearchChange={vi.fn()}
      allTotal={2}
      allAvailable={2}
      allSelected={0}
    />,
  );
}

describe('DeviceNodeRail 节点状态圆点（#2599）', () => {
  it('在线 → 成功色 + 「在线」', () => {
    const { container } = renderRail([node({ online: true })]);
    expect(screen.getByTitle('在线')).toHaveClass('bg-success');
    expect(container.querySelector('.bg-muted-foreground\\/40')).toBeNull();
  });

  it('离线（host 记录在、状态非 ONLINE）→ 仍走破坏色 + 「离线」', () => {
    renderRail([node({ online: false })]);
    expect(screen.getByTitle('离线')).toHaveClass('bg-destructive');
  });

  it('host 记录缺失（online=null）→ 中性色 + 「节点信息未知」，**不冒充在线**', () => {
    // 旧行为 `!host || host.status === 'ONLINE'` 把「不知道」判成在线（fail-open），
    // 新节点会以健康绿点进入选机决策。这条用例去掉 fail-closed 即红。
    renderRail([node({ online: null })]);
    const dot = screen.getByTitle('节点信息未知（主机记录缺失）');
    expect(dot).toHaveClass('bg-muted-foreground/40');
    expect(dot).not.toHaveClass('bg-success');
    expect(dot).not.toHaveClass('bg-destructive');
  });
});
