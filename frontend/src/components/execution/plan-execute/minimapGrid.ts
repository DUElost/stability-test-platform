/** Minimap 列数估算（与 DeviceMatrix 固定像素列策略一致）。 */

export const MINIMAP_TILE_GAP = 4;
/** 右栏已选集固定宽度（与 DeviceWorkspace 280px 列对齐）。 */
export const MINIMAP_EMBEDDED_RAIL_INNER_WIDTH = 280 - 24;

export function measureMinimapColumns(
  width: number,
  tileMin: number,
  gap: number = MINIMAP_TILE_GAP,
): number {
  if (width <= 0) return 0;
  return Math.max(1, Math.floor((width + gap) / (tileMin + gap)));
}

export function defaultMinimapColumns(
  embedded: boolean,
  tileMin: number,
  gap: number = MINIMAP_TILE_GAP,
): number {
  const fallbackWidth = embedded ? MINIMAP_EMBEDDED_RAIL_INNER_WIDTH : 480;
  return measureMinimapColumns(fallbackWidth, tileMin, gap) || 6;
}
