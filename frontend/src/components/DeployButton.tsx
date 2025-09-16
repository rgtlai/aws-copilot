import React from 'react';
import { Button } from '@/components/ui/button';
import { Rocket, Loader2, CheckCircle, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

interface DeployButtonProps {
  onClick: () => void;
  status: 'idle' | 'deploying' | 'success' | 'error';
  disabled?: boolean;
}

export const DeployButton: React.FC<DeployButtonProps> = ({ onClick, status, disabled }) => {
  const getButtonContent = () => {
    switch (status) {
      case 'deploying':
        return (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Deploying...
          </>
        );
      case 'success':
        return (
          <>
            <CheckCircle className="h-4 w-4" />
            Deployed
          </>
        );
      case 'error':
        return (
          <>
            <XCircle className="h-4 w-4" />
            Retry Deploy
          </>
        );
      default:
        return (
          <>
            <Rocket className="h-4 w-4" />
            Deploy to AWS
          </>
        );
    }
  };

  const getButtonVariant = () => {
    switch (status) {
      case 'success':
        return 'default';
      case 'error':
        return 'destructive';
      default:
        return 'default';
    }
  };

  return (
    <Button
      onClick={onClick}
      disabled={disabled || status === 'deploying'}
      variant={getButtonVariant()}
      className={cn(
        "gap-2 font-mono font-semibold min-w-[140px] transition-all duration-300",
        "bg-gradient-primary hover:opacity-90 text-primary-foreground",
        status === 'idle' && "shadow-glow animate-pulse",
        status === 'success' && "bg-green-600 hover:bg-green-700",
        status === 'error' && "bg-destructive hover:bg-destructive/90"
      )}
    >
      {getButtonContent()}
    </Button>
  );
};