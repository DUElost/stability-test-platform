import { describe, expect, it } from 'vitest';
import type { PlanStep } from '@/utils/api/types';
import { analyzePlanNetworkRequirements } from './networkPoolRequirements';

function makeStep(scriptName: string, params: Record<string, unknown> = {}, enabled = true): PlanStep {
  return {
    id: 1,
    step_key: scriptName,
    script_name: scriptName,
    script_version: '1.0.0',
    stage: 'init',
    sort_order: 0,
    params,
    timeout_seconds: 60,
    retry: 0,
    enabled,
  };
}

describe('analyzePlanNetworkRequirements', () => {
  it('detects legacy connect_wifi / monkey_setup as wifi-only', () => {
    const res = analyzePlanNetworkRequirements([makeStep('connect_wifi')]);
    expect(res.hasNetworkSteps).toBe(true);
    expect(res.isLegacyOnly).toBe(true);
    expect(res.requiredType).toBe('wifi');
    expect(res.requiredEgress).toBeNull();
    expect(res.conflictReason).toBeNull();
  });

  it('derives setup_network direct / vpn and type filters', () => {
    const res = analyzePlanNetworkRequirements([
      makeStep('setup_network', { required_type: 'usb_net', required_egress: 'direct', required: true }),
    ]);
    expect(res.hasNetworkSteps).toBe(true);
    expect(res.isLegacyOnly).toBe(false);
    expect(res.requiredType).toBe('usb_net');
    expect(res.requiredEgress).toBe('direct');
    expect(res.setupRequired).toBe(true);
    expect(res.conflictReason).toBeNull();
  });

  it('flags conflicting mixed steps (usb_net + connect_wifi)', () => {
    const res = analyzePlanNetworkRequirements([
      makeStep('setup_network', { required_type: 'usb_net' }),
      makeStep('connect_wifi'),
    ]);
    expect(res.hasNetworkSteps).toBe(true);
    expect(res.conflictReason).toContain('交集为空');
  });

  it('flags usb_net + vpn Phase 1 block', () => {
    const res = analyzePlanNetworkRequirements([
      makeStep('setup_network', { required_type: 'usb_net', required_egress: 'vpn' }),
    ]);
    expect(res.conflictReason).toContain('Phase 1');
  });

  it('ignores disabled steps when computing intersection', () => {
    const res = analyzePlanNetworkRequirements([
      makeStep('setup_network', { required_type: 'usb_net', required_egress: 'direct' }, true),
      makeStep('connect_wifi', {}, false),
    ]);
    expect(res.requiredType).toBe('usb_net');
    expect(res.conflictReason).toBeNull();
  });
});
