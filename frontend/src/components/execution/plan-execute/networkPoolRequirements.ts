import type { PlanStep } from '@/utils/api/types';

export type RequiredNetworkType = 'wifi' | 'usb_net';
export type RequiredNetworkEgress = 'direct' | 'vpn';

export interface PlanNetworkRequirements {
  hasNetworkSteps: boolean;
  hasSetupNetwork: boolean;
  isLegacyOnly: boolean;
  allowedTypes: RequiredNetworkType[];
  allowedEgresses: RequiredNetworkEgress[];
  /** Query value for /api/v1/resource-pools/available (`required_type`) when narrowed to one type. */
  requiredType: RequiredNetworkType | null;
  /** Query value for /api/v1/resource-pools/available (`required_egress`) when narrowed to one egress. */
  requiredEgress: RequiredNetworkEgress | null;
  setupRequired: boolean;
  conflictReason: string | null;
}

const VALID_TYPES = new Set<string>(['wifi', 'usb_net']);
const VALID_EGRESSES = new Set<string>(['direct', 'vpn']);
const NETWORK_CONSUMER_SCRIPTS = new Set(['setup_network', 'connect_wifi', 'monkey_setup']);

export function analyzePlanNetworkRequirements(
  steps: ReadonlyArray<PlanStep> | undefined | null,
): PlanNetworkRequirements {
  if (!steps || steps.length === 0) {
    return {
      hasNetworkSteps: false,
      hasSetupNetwork: false,
      isLegacyOnly: false,
      allowedTypes: [],
      allowedEgresses: [],
      requiredType: null,
      requiredEgress: null,
      setupRequired: false,
      conflictReason: null,
    };
  }

  let hasSetupNetwork = false;
  let hasConnectWifi = false;
  let hasMonkeySetup = false;
  let allowedTypes = new Set<RequiredNetworkType>(['wifi', 'usb_net']);
  let allowedEgresses = new Set<RequiredNetworkEgress>(['direct', 'vpn']);
  let setupRequired = false;
  let conflictReason: string | null = null;

  for (const step of steps) {
    if (step.enabled === false) continue;
    if (!NETWORK_CONSUMER_SCRIPTS.has(step.script_name)) continue;
    const params = (step.params ?? {}) as Record<string, unknown>;

    if (step.script_name === 'setup_network') {
      hasSetupNetwork = true;
      const rawType = typeof params.required_type === 'string' ? params.required_type.trim() : '';
      let stepTypes: Set<RequiredNetworkType>;
      if (!rawType) {
        stepTypes = new Set<RequiredNetworkType>(['wifi', 'usb_net']);
      } else if (VALID_TYPES.has(rawType)) {
        stepTypes = new Set<RequiredNetworkType>([rawType as RequiredNetworkType]);
      } else {
        stepTypes = new Set();
        conflictReason = `不支持的 setup_network required_type: ${rawType}`;
      }

      const rawEgress = typeof params.required_egress === 'string' ? params.required_egress.trim() : '';
      const egressVal = rawEgress || 'direct';
      let stepEgresses: Set<RequiredNetworkEgress>;
      if (VALID_EGRESSES.has(egressVal)) {
        stepEgresses = new Set<RequiredNetworkEgress>([egressVal as RequiredNetworkEgress]);
      } else {
        stepEgresses = new Set();
        conflictReason = `不支持的 setup_network required_egress: ${egressVal}`;
      }

      if (params.required) setupRequired = true;
      allowedTypes = new Set([...allowedTypes].filter((t) => stepTypes.has(t)));
      allowedEgresses = new Set([...allowedEgresses].filter((e) => stepEgresses.has(e)));
    } else if (step.script_name === 'connect_wifi') {
      hasConnectWifi = true;
      allowedTypes = new Set([...allowedTypes].filter((t) => t === 'wifi'));
    } else if (step.script_name === 'monkey_setup') {
      hasMonkeySetup = true;
      allowedTypes = new Set([...allowedTypes].filter((t) => t === 'wifi'));
    }
  }

  const hasNetwork = hasSetupNetwork || hasConnectWifi || hasMonkeySetup;
  const isLegacyOnly = hasNetwork && !hasSetupNetwork;

  if (!hasNetwork) {
    return {
      hasNetworkSteps: false,
      hasSetupNetwork: false,
      isLegacyOnly: false,
      allowedTypes: [],
      allowedEgresses: [],
      requiredType: null,
      requiredEgress: null,
      setupRequired: false,
      conflictReason: null,
    };
  }

  const types = Array.from(allowedTypes);
  const egresses = Array.from(allowedEgresses);

  if (!conflictReason) {
    if (types.length === 0) {
      conflictReason = '当前 Plan 启用步骤的网络供给类型交集为空（如 usb_net 与 connect_wifi/monkey_setup 不能混用）';
    } else if (egresses.length === 0) {
      conflictReason = '当前 Plan 启用步骤的网络出口策略冲突（direct 与 vpn 不能同时强制要求）';
    } else if (types.length === 1 && types[0] === 'usb_net' && egresses.length === 1 && egresses[0] === 'vpn') {
      conflictReason = '当前 Phase 1 暂未开放 usb_net + VPN 出口组合';
    }
  }

  return {
    hasNetworkSteps: true,
    hasSetupNetwork,
    isLegacyOnly,
    allowedTypes: types,
    allowedEgresses: egresses,
    requiredType: types.length === 1 ? types[0] : null,
    requiredEgress: egresses.length === 1 ? egresses[0] : null,
    setupRequired,
    conflictReason,
  };
}

export function poolEgressOf(config: Record<string, unknown> | undefined | null): 'direct' | 'vpn' {
  const raw = typeof config?.egress === 'string' ? config.egress.trim().toLowerCase() : '';
  return raw === 'vpn' ? 'vpn' : 'direct';
}

export function formatNetworkPoolTypeLabel(resourceType: string): string {
  return resourceType === 'usb_net' ? 'USB 反向供网' : 'WiFi';
}

export function formatNetworkEgressLabel(egress: string): string {
  return egress === 'vpn' ? 'VPN 出口' : '普通直连';
}
