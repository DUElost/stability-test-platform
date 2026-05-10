import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';

interface StableResponsiveContainerProps {
  children: ReactNode;
  className?: string;
}

/**
 * 尺寸就绪后挂载子节点，从源头消除 Recharts width(-1)/height(-1) warning。
 *
 * 为什么用 useLayoutEffect 而不是 useEffect：
 *   useEffect 在浏览器绘制后异步执行 → 首帧 children 不渲染 →
 *   下一帧渲染 children 时 ResponsiveContainer 挂载，但此时父级尺寸可能仍为 0。
 *
 *   useLayoutEffect 在 DOM 提交后、浏览器绘制前同步执行 →
 *   如果尺寸已就绪，setReady(true) 的 re-render 在同一帧内完成，
 *   ResponsiveContainer 挂载时父级已有最终尺寸。
 */
export function StableResponsiveContainer({
  children,
  className = 'h-[200px] min-h-[200px] w-full',
}: StableResponsiveContainerProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;

    const check = (): boolean => {
      if (node.offsetWidth > 0 && node.offsetHeight > 0) {
        setReady(true);
        return true;
      }
      return false;
    };

    // 同步检查：布局已计算，offsetHeight 反映实际渲染高度
    if (check()) return;

    // 未就绪：ResizeObserver 兜底
    const observer = new ResizeObserver(() => {
      if (check()) observer.disconnect();
    });
    observer.observe(node);

    // requestAnimationFrame 兜底（极端情况：首帧 observer 未触发）
    let raf = requestAnimationFrame(() => {
      raf = 0;
      check();
    });

    return () => {
      observer.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <div ref={ref} className={className}>
      {ready ? children : null}
    </div>
  );
}
