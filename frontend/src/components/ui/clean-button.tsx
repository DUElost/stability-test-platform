import { ButtonHTMLAttributes, forwardRef } from 'react';
import { cn } from '@/lib/utils';

interface CleanButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'primary' | 'danger' | 'ghost';
  size?: 'sm' | 'md';
}

/**
 * 极简按钮 - 源自 web 样板设计
 */
const CleanButton = forwardRef<HTMLButtonElement, CleanButtonProps>(
  ({ children, variant = 'default', size = 'md', className = '', ...props }, ref) => {
    const variantClasses = {
      default: 'bg-gray-50 text-gray-700 hover:bg-gray-100 border border-gray-200',
      primary: 'bg-gray-900 text-white hover:bg-gray-800',
      danger: 'bg-red-50 text-red-600 hover:bg-red-100 border border-red-100',
      ghost: 'text-gray-500 hover:text-gray-900 hover:bg-gray-50',
    };

    const sizeClasses = {
      sm: 'px-3 py-1.5 text-xs',
      md: 'px-4 py-2 text-sm',
    };

    return (
      <button
        ref={ref}
        className={cn(
          'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-all duration-200',
          variantClasses[variant],
          sizeClasses[size],
          className
        )}
        {...props}
      >
        {children}
      </button>
    );
  }
);

CleanButton.displayName = 'CleanButton';

export { CleanButton };
