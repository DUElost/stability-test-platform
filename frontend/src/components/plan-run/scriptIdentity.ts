/**
 * #3350（ADR-0023 D2/D3）：脚本身份的展示口径。
 *
 * 统一为 `name@version`；版本为空（旧快照)只显示 name；name 为空返回 null
 * （调用方决定显示 `—` 还是不渲染）——四处消费点共用同一口径，避免各写一套。
 */
export function formatScriptIdentity(
  name?: string | null,
  version?: string | null,
): string | null {
  if (!name) return null;
  return version ? `${name}@${version}` : name;
}
