import React from 'react';
import { cn } from '@/lib/utils';
import type { Message } from './DeploymentChatbot';
import { Button } from '@/components/ui/button';

interface ChatMessageProps {
  message: Message;
}

const formatContent = (text: string) => {
  const segments: React.ReactNode[] = [];
  const lines = text.split('\n');

  let listBuffer: { items: string[]; ordered: boolean } | null = null;

  const flushList = () => {
    if (listBuffer && listBuffer.items.length > 0) {
      const ListTag = listBuffer.ordered ? 'ol' : 'ul';
      segments.push(
        <ListTag
          key={`list-${segments.length}`}
          className={cn(
            'mb-2 ml-5 space-y-1 text-sm last:mb-0',
            listBuffer.ordered ? 'list-decimal' : 'list-disc'
          )}
        >
          {listBuffer.items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ListTag>
      );
    }
    listBuffer = null;
  };

  lines.forEach((rawLine, index) => {
    const line = rawLine.trim();

    if (!line) {
      flushList();
      return;
    }

    const bulletMatch = line.match(/^([-*])\s+(.*)$/);
    const orderedMatch = line.match(/^(\d+)\.\s+(.*)$/);

    if (bulletMatch) {
      const [, , content] = bulletMatch;
      if (!listBuffer || listBuffer.ordered) {
        flushList();
        listBuffer = { items: [], ordered: false };
      }
      listBuffer.items.push(content.trim());
      return;
    }

    if (orderedMatch) {
      const [, , content] = orderedMatch;
      if (!listBuffer || !listBuffer.ordered) {
        flushList();
        listBuffer = { items: [], ordered: true };
      }
      listBuffer.items.push(content.trim());
      return;
    }

    flushList();
    segments.push(
      <p key={`p-${index}`} className="mb-2 text-sm leading-relaxed last:mb-0">
        {line}
      </p>
    );
  });

  flushList();

  if (segments.length === 0) {
    return [
      <p key="fallback" className="text-sm leading-relaxed">
        {text}
      </p>,
    ];
  }

  return segments;
};

export const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const isUser = message.sender === 'user';
  const isAssistant = message.sender === 'assistant';
  const [showThoughts, setShowThoughts] = React.useState(false);
  const contentSegments = formatContent(message.content);

  React.useEffect(() => {
    setShowThoughts(false);
  }, [message.id]);

  return (
    <div className={cn('flex gap-3', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && (
        <div className="w-8 h-8 bg-gradient-primary rounded-full flex items-center justify-center flex-shrink-0 shadow-glow">
          <span className="text-primary-foreground font-bold text-xs">AI</span>
        </div>
      )}
      
      <div className={cn(
        'max-w-[80%] rounded-2xl px-4 py-3 shadow-card transition-all duration-300',
        isUser 
          ? 'bg-gradient-primary text-primary-foreground' 
          : 'bg-gradient-card border border-border'
      )}>
        <div className={cn(isUser ? 'text-primary-foreground' : 'text-foreground')}>{contentSegments}</div>
        <p
          className={cn(
            'mt-3 text-xs font-mono uppercase tracking-wide opacity-70',
            isUser ? 'text-primary-foreground' : 'text-muted-foreground'
          )}
        >
          {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </p>

        {isAssistant && message.thoughts && message.thoughts.length > 0 && (
          <div className="mt-3 border-t border-border/60 pt-3">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-xs"
              onClick={() => setShowThoughts((prev) => !prev)}
            >
              {showThoughts ? 'Hide reasoning' : 'Show reasoning'}
            </Button>
            {showThoughts && (
              <div className="mt-3 space-y-3 text-sm text-muted-foreground">
                {message.thoughts.map((thought, index) => (
                  <div key={`${message.id}-thought-${index}`} className="rounded-xl border border-border/40 bg-background/60 p-3">
                    {formatContent(thought)}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {isUser && (
        <div className="w-8 h-8 bg-secondary rounded-full flex items-center justify-center flex-shrink-0">
          <span className="text-secondary-foreground font-bold text-xs">U</span>
        </div>
      )}
    </div>
  );
};
