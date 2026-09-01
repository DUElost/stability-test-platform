import { cn } from '@/lib/utils';
import logo32 from '@/assets/brand/logo-32.png';
import logo40 from '@/assets/brand/logo-40.png';
import logo64 from '@/assets/brand/logo-64.png';
import logo96 from '@/assets/brand/logo-96.png';

const SRC = {
  32: logo32,
  40: logo40,
  64: logo64,
  96: logo96,
} as const;

type BrandLogoSize = keyof typeof SRC;

interface BrandLogoProps {
  size?: BrandLogoSize;
  className?: string;
  /** Decorative when paired with adjacent text; set false if logo is the only label. */
  decorative?: boolean;
}

/** Platform mark — square crop of the official logo (PNG, no wide canvas padding). */
export function BrandLogo({
  size = 32,
  className,
  decorative = true,
}: BrandLogoProps) {
  return (
    <img
      src={SRC[size]}
      width={size}
      height={size}
      alt={decorative ? '' : '稳定性测试平台'}
      aria-hidden={decorative || undefined}
      draggable={false}
      className={cn('shrink-0 rounded-lg select-none', className)}
    />
  );
}

export default BrandLogo;
