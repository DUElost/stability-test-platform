/** Display aliases for observed model/product pairs; never a flashing permission. */
export function deviceProductLabel(model?: string | null, product?: string | null): string | null {
  const normalizedModel = model?.replace(/-/g, '_').toUpperCase();
  if (normalizedModel === 'MLD_NX2' && product === 'MLD-NX2') return 'NX2';
  if (normalizedModel === 'MLD_NX3') {
    if (product === 'MLD-NX3') return 'NX3';
    if (product === 'sys_mssi_64_cn_armv82') return 'NX3Carrier';
  }
  return null;
}

export function deviceProductText(model?: string | null, product?: string | null): string {
  if (!product) return '未上报';
  const label = deviceProductLabel(model, product);
  return label ? `${label} · ${product}` : product;
}
