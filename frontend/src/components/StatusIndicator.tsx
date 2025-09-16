import React from 'react';
import { cn } from '@/lib/utils';
import { Loader2, CheckCircle, XCircle, Circle } from 'lucide-react';

interface StatusIndicatorProps {
  status: 'idle' | 'deploying' | 'success' | 'error';
}

export const StatusIndicator: React.FC<StatusIndicatorProps> = ({ status }) => {
  const getStatusContent = () => {
    switch (status) {
      case 'deploying':
        return {
          icon: <Loader2 className="h-4 w-4 animate-spin" />,
          text: 'Deploying',
          className: 'text-accent border-accent/30 bg-accent/10'
        };
      case 'success':
        return {
          icon: <CheckCircle className="h-4 w-4" />,
          text: 'Deployed',
          className: 'text-green-400 border-green-400/30 bg-green-400/10'
        };
      case 'error':
        return {
          icon: <XCircle className="h-4 w-4" />,
          text: 'Failed',
          className: 'text-destructive border-destructive/30 bg-destructive/10'
        };
      default:
        return {
          icon: <Circle className="h-4 w-4" />,
          text: 'Ready',
          className: 'text-muted-foreground border-border bg-card'
        };
    }
  };

  const { icon, text, className } = getStatusContent();

  return (
    <div className={cn(
      "flex items-center gap-2 px-3 py-1.5 rounded-full border transition-all duration-300",
      "font-mono text-sm font-medium",
      className
    )}>
      {icon}
      <span>{text}</span>
    </div>
  );
};