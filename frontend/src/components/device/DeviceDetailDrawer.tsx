import { useState } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import {
  Battery,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  ExternalLink,
  HardDrive,
  Pencil,
  Server,
  Smartphone,
  Sparkles,
  Tag,
  Thermometer,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { StatusBadge } from '@/components/ui/status-badge';
import { cn } from '@/lib/utils';
import { resourceUsageBgClass, resourceUsageTextClass, TEXT } from '@/design-system/tokens';
import { deviceProductText } from '@/utils/deviceProduct';
import { formatBytes, formatDateTimeFull } from '@/utils/format';
import type { DeviceUsbPresence } from '@/utils/api/types';
import { DeviceReturnPanel } from './DeviceReturnPanel';
import type { DeviceTableData } from './ExpandableDeviceTable';

interface DeviceDetailDrawerProps {
  device: DeviceTableData | null;
  onClose: () => void;
  isAdmin?: boolean;
  onEditDeviceTags?: (device: DeviceTableData) => void;
  onNavigateToHost?: (hostName: string) => void;
  /** 列表内上下翻页导航（基于当前筛选排序后的设备序列） */
  currentIndex?: number;
  totalCount?: number;
  onPrev?: () => void;
  onNext?: () => void;
}

const USB_PRESENCE_META: Record<
  DeviceUsbPresence['state'],
  { text: string; className: string; hint: string }
> = {
  powered_off: {
    text: 'USB 关机',
    className: 'bg-info/15 text-info',
    hint: '槽位口为关机态（0e8d:2046），可走 USB 切电刷机',
  },
  adb_mode: {
    text: 'USB 开机',
    className: 'bg-success/15 text-success',
    hint: '槽位口为开机 ADB 态（0e8d:201c）',
  },
  other: {
    text: 'USB 其他',
    className: 'bg-warning/20 text-warning',
    hint: '槽位口有枚举，但不是已知的关机或开机态',
  },
  absent: {
    text: 'USB 空口',
    className: 'bg-destructive/15 text-destructive-text',
    hint: '槽位口下没有 USB 设备：检查连线或 Hub 供电',
  },
};

function temperatureTextClass(temperature: number): string {
  if (temperature > 45) return 'text-destructive-text';
  if (temperature > 40) return 'text-warning';
  return 'text-foreground';
}

function latencyTextClass(latency: number): string {
  if (latency > 300) return 'text-destructive-text';
  if (latency > 120) return 'text-warning';
  return 'text-success';
}

function computeDisk(device: DeviceTableData) {
  const total = device.disk_total;
  const used = device.disk_used;
  if (total == null || used == null || total <= 0) return null;
  return {
    pct: Math.min(100, (used / total) * 100),
    total,
    used,
    free: Math.max(0, total - used),
  };
}

/**
 * 物理设备右侧标准详情抽屉（#4117 U2-1b / D2 / #465）。
 *
 * 替代行内表格展开：
 * - 基于标准 `ui/sheet.tsx`（size="lg"，焦点陷阱、Escape 关闭与触发行焦点归位）；
 * - 仅打开时按需挂载 `DeviceReturnPanel` 拉取 `return-debts`；
 * - 支持列表内上一台 / 下一台连续巡检、一键复制串号、项目/主机/AI 助手上下文深链。
 */
export function DeviceDetailDrawer({
  device,
  onClose,
  isAdmin = false,
  onEditDeviceTags,
  onNavigateToHost,
  currentIndex,
  totalCount,
  onPrev,
  onNext,
}: DeviceDetailDrawerProps) {
  const inRouter = useInRouterContext();
  const [copiedSerial, setCopiedSerial] = useState<string | null>(null);

  const handleCopySerial = async (serial: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(serial);
      }
      setCopiedSerial(serial);
      window.setTimeout(() => {
        setCopiedSerial((prev) => (prev === serial ? null : prev));
      }, 1500);
    } catch {
      // ignore clipboard denial
    }
  };

  const disk = device ? computeDisk(device) : null;
  const usbMeta = device?.usb_presence
    ? (USB_PRESENCE_META[device.usb_presence.state] ?? {
        text: 'USB ?',
        className: 'bg-muted text-muted-foreground',
        hint: '未知 USB 状态',
      })
    : null;
  const aiPrompt = device
    ? `请协助诊断物理设备 ${device.serial}（ID #${device.id}，型号 ${device.model}，状态 ${device.status}，主机 ${device.host_name || '未分配'}）的当前健康状态与可能异常。`
    : '';

  return (
    <Sheet
      open={Boolean(device)}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose();
      }}
    >
      {device ? (
        <SheetContent
          side="right"
          size="lg"
          hideCloseButton
          overlayTestId="fleet-device-drawer-overlay"
          data-testid="fleet-device-drawer"
          aria-label={`设备 ${device.serial} 详情`}
        >
          <SheetHeader className="flex-row items-start justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-1">
              <SheetDescription className="flex flex-wrap items-center gap-1.5 text-xs">
                <span className="font-mono">Device #{device.id}</span>
                {device.host_name ? (
                  <>
                    <span>·</span>
                    {onNavigateToHost ? (
                      <button
                        type="button"
                        onClick={() => onNavigateToHost(device.host_name!)}
                        className="inline-flex items-center gap-1 text-primary hover:underline"
                      >
                        <Server className="h-3 w-3" />
                        {device.host_name}
                      </button>
                    ) : (
                      <span>{device.host_name}</span>
                    )}
                  </>
                ) : null}
                {device.project_key ? (
                  <>
                    <span>·</span>
                    {inRouter ? (
                      <Link
                        to={`/projects/${encodeURIComponent(device.project_key)}`}
                        className="font-mono text-primary hover:underline"
                      >
                        {device.project_key}
                      </Link>
                    ) : (
                      <span className="font-mono">{device.project_key}</span>
                    )}
                  </>
                ) : null}
              </SheetDescription>

              <div className="flex flex-wrap items-center gap-2">
                <SheetTitle className="truncate font-mono text-base font-semibold">
                  {device.serial}
                </SheetTitle>
                <button
                  type="button"
                  onClick={() => void handleCopySerial(device.serial)}
                  aria-label="复制设备序列号"
                  title="复制设备序列号"
                  className="inline-flex h-6 w-6 items-center justify-center rounded border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  {copiedSerial === device.serial ? (
                    <Check className="h-3.5 w-3.5 text-success" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                </button>
                <StatusBadge kind="device-ui" status={device.status} size="sm" />
                {device.retired_at ? (
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                    已退役
                  </span>
                ) : null}
                {!device.retired_at && device.is_stale ? (
                  <span className="rounded-full bg-warning/20 px-2 py-0.5 text-[11px] text-warning">
                    陈旧
                  </span>
                ) : null}
                {device.adb_state === 'unauthorized' ? (
                  <span className="rounded-full bg-destructive/15 px-2 py-0.5 text-[11px] text-destructive-text">
                    未授权
                  </span>
                ) : null}
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-1">
              {currentIndex != null && totalCount != null && totalCount > 1 ? (
                <div className="mr-1 flex items-center gap-0.5 rounded-md border border-border bg-muted/40 p-0.5">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label="上一台设备"
                    data-testid="fleet-device-drawer-prev"
                    disabled={!onPrev || currentIndex <= 0}
                    onClick={onPrev}
                    className="h-6 w-6 p-0"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </Button>
                  <span className="px-1.5 font-mono text-[11px] tabular-nums text-muted-foreground">
                    {currentIndex + 1}/{totalCount}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label="下一台设备"
                    data-testid="fleet-device-drawer-next"
                    disabled={!onNext || currentIndex >= totalCount - 1}
                    onClick={onNext}
                    className="h-6 w-6 p-0"
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onClose}
                data-testid="fleet-device-drawer-close"
                aria-label="关闭设备详情"
                className="h-7 w-7 p-0"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </SheetHeader>

          <SheetBody className="space-y-4">
            {/* 1. 归属与硬件信息 */}
            <section className="rounded-lg border border-border bg-card p-3.5">
              <div className="mb-2.5 flex items-center gap-2">
                <Smartphone className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-medium text-foreground">设备信息与归属</h3>
              </div>
              <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
                <div className="flex items-center justify-between gap-2">
                  <dt className={TEXT.subtitle}>型号</dt>
                  <dd className="truncate text-right font-medium text-foreground">{device.model}</dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className={TEXT.subtitle}>Product</dt>
                  <dd
                    className="max-w-[65%] truncate text-right font-mono text-foreground"
                    title={deviceProductText(device.model, device.product)}
                  >
                    {deviceProductText(device.model, device.product)}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className={TEXT.subtitle}>系统版本</dt>
                  <dd
                    className="max-w-[65%] truncate text-right font-mono text-foreground"
                    title={device.build_display_id ?? undefined}
                  >
                    {device.build_display_id || '—'}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className={TEXT.subtitle}>归属项目</dt>
                  <dd className="text-right font-mono text-foreground">
                    {device.project_key ? (
                      inRouter ? (
                        <Link
                          to={`/projects/${encodeURIComponent(device.project_key)}`}
                          className="inline-flex items-center gap-1 text-primary hover:underline"
                        >
                          {device.project_key}
                          <ExternalLink className="h-3 w-3" />
                        </Link>
                      ) : (
                        device.project_key
                      )
                    ) : (
                      <span className={TEXT.subtitle}>未归属</span>
                    )}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className={TEXT.subtitle}>所属主机</dt>
                  <dd className="truncate text-right text-foreground">
                    {device.host_name && onNavigateToHost ? (
                      <button
                        type="button"
                        onClick={() => onNavigateToHost(device.host_name!)}
                        className="inline-flex items-center gap-1 text-primary hover:underline"
                      >
                        {device.host_name}
                        <ExternalLink className="h-3 w-3" />
                      </button>
                    ) : (
                      device.host_name || '—'
                    )}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className={TEXT.subtitle}>ADB 状态</dt>
                  <dd className="font-mono text-foreground">{device.adb_state || '—'}</dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className={TEXT.subtitle}>最后活跃</dt>
                  <dd className="font-mono text-foreground">
                    {device.last_seen ? formatDateTimeFull(device.last_seen) : '—'}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className={cn('flex items-center gap-1', TEXT.subtitle)}>
                    <Tag className="h-3 w-3" />
                    标签
                  </dt>
                  <dd className="flex min-w-0 justify-end">
                    {onEditDeviceTags ? (
                      <button
                        type="button"
                        data-testid={`device-detail-edit-tags-${device.id}`}
                        onClick={() => onEditDeviceTags(device)}
                        className="inline-flex max-w-full items-center gap-1 truncate rounded px-1.5 py-0.5 text-right text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        title={device.tags?.length ? `编辑标签：${device.tags.join(', ')}` : '点击添加标签'}
                      >
                        <span className={cn('truncate', !device.tags?.length && 'text-muted-foreground')}>
                          {device.tags?.length ? device.tags.join(', ') : '点击添加标签'}
                        </span>
                        <Pencil className="h-3 w-3 shrink-0 text-muted-foreground" />
                      </button>
                    ) : (
                      <span
                        className="truncate text-right text-foreground"
                        title={device.tags?.join(', ') || undefined}
                      >
                        {device.tags?.length ? device.tags.join(', ') : '—'}
                      </span>
                    )}
                  </dd>
                </div>
              </dl>

              {device.usb_presence && usbMeta ? (
                <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border/60 pt-2.5 text-xs">
                  <span className={TEXT.subtitle}>VBUS 槽位</span>
                  <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', usbMeta.className)}>
                    {usbMeta.text}
                  </span>
                  <span className="font-mono text-foreground">{device.usb_presence.slot}</span>
                  <span className={cn('font-mono', TEXT.subtitle)}>
                    VID:PID {device.usb_presence.vid_pid ?? '无'}
                  </span>
                  {!device.usb_presence.host_online ? (
                    <span className="text-[11px] text-warning">主机离线（数据可能已过期）</span>
                  ) : null}
                </div>
              ) : null}
            </section>

            {/* 2. 设备归还状态（仅抽屉打开时挂载并按需拉取 return-debts） */}
            <section className="rounded-lg border border-border bg-card p-3.5">
              <DeviceReturnPanel
                deviceId={device.id}
                returnState={device.return_state}
                isAdmin={isAdmin}
              />
            </section>

            {/* 3. 实时遥测指标 */}
            <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {/* 电量 */}
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-2 flex items-center gap-2">
                  <Battery className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium text-foreground">电量</span>
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between text-xs">
                    <span className={TEXT.subtitle}>当前电量</span>
                    <span
                      className={cn(
                        'font-mono font-medium',
                        device.battery_level != null
                          ? resourceUsageTextClass(100 - device.battery_level)
                          : TEXT.subtitle,
                      )}
                    >
                      {device.battery_level != null ? `${device.battery_level.toFixed(0)}%` : '—'}
                    </span>
                  </div>
                  <Progress
                    value={device.battery_level ?? 0}
                    className="h-2"
                    indicatorClassName={resourceUsageBgClass(100 - (device.battery_level ?? 0))}
                  />
                </div>
              </div>

              {/* 温度 */}
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-2 flex items-center gap-2">
                  <Thermometer className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium text-foreground">温度</span>
                </div>
                <div className="flex items-baseline gap-1.5">
                  <span
                    className={cn(
                      'font-mono text-2xl font-semibold',
                      device.temperature != null ? temperatureTextClass(device.temperature) : 'text-foreground',
                    )}
                  >
                    {device.temperature != null ? device.temperature.toFixed(1) : '—'}
                  </span>
                  {device.temperature != null ? (
                    <span className="text-xs text-muted-foreground">°C</span>
                  ) : null}
                </div>
              </div>

              {/* 存储空间 */}
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-2 flex items-center gap-2">
                  <HardDrive className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium text-foreground">存储空间</span>
                </div>
                {disk ? (
                  <div className="space-y-1.5">
                    <div className="flex justify-between text-xs">
                      <span className={TEXT.subtitle}>已用 {formatBytes(disk.used)}</span>
                      <span className={cn('font-mono font-medium', resourceUsageTextClass(disk.pct))}>
                        {disk.pct.toFixed(0)}%
                      </span>
                    </div>
                    <Progress
                      value={disk.pct}
                      className="h-2"
                      indicatorClassName={resourceUsageBgClass(disk.pct)}
                    />
                    <div className="flex justify-between text-xs">
                      <span className={TEXT.subtitle}>可用</span>
                      <span className="font-mono text-foreground">{formatBytes(disk.free)}</span>
                    </div>
                    <div className="flex justify-between text-xs">
                      <span className={TEXT.subtitle}>总容量 (/data)</span>
                      <span className="font-mono text-foreground">{formatBytes(disk.total)}</span>
                    </div>
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground/70">未上报（等待设备容量采样）</div>
                )}
              </div>

              {/* 网络延迟 */}
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-2 flex items-center gap-2">
                  {device.network_latency != null ? (
                    <Wifi className="h-4 w-4 text-success" />
                  ) : (
                    <WifiOff className="h-4 w-4 text-muted-foreground" />
                  )}
                  <span className="text-sm font-medium text-foreground">网络延迟</span>
                </div>
                <div className="flex items-baseline gap-1.5">
                  <span
                    className={cn(
                      'font-mono text-2xl font-semibold',
                      device.network_latency != null
                        ? latencyTextClass(device.network_latency)
                        : 'text-foreground',
                    )}
                  >
                    {device.network_latency != null ? device.network_latency.toFixed(0) : '—'}
                  </span>
                  {device.network_latency != null ? (
                    <span className="text-xs text-muted-foreground">ms</span>
                  ) : null}
                </div>
              </div>
            </section>
          </SheetBody>

          <SheetFooter className="justify-between">
            {inRouter ? (
              <Button variant="outline" size="sm" asChild className="gap-1.5">
                <Link to={`/assistant?prompt=${encodeURIComponent(aiPrompt)}`}>
                  <Sparkles className="h-3.5 w-3.5 text-primary" />
                  向 AI 助手提问
                </Link>
              </Button>
            ) : (
              <span />
            )}
            <Button type="button" variant="outline" size="sm" onClick={onClose}>
              关闭
            </Button>
          </SheetFooter>
        </SheetContent>
      ) : null}
    </Sheet>
  );
}
