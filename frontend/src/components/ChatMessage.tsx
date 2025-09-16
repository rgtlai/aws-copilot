import React from 'react';
import { cn } from '@/lib/utils';
import type { Message } from './DeploymentChatbot';

interface ChatMessageProps {
  message: Message;
}

export const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const isUser = message.sender === 'user';

  return (
    <div className={cn("flex gap-3", isUser ? "justify-end" : "justify-start")}>
      {!isUser && (
        <div className="w-8 h-8 bg-gradient-primary rounded-full flex items-center justify-center flex-shrink-0 shadow-glow">
          <span className="text-primary-foreground font-bold text-xs">AI</span>
        </div>
      )}
      
      <div className={cn(
        "max-w-[80%] rounded-2xl px-4 py-3 shadow-card transition-all duration-300",
        isUser 
          ? "bg-gradient-primary text-primary-foreground" 
          : "bg-gradient-card border border-border"
      )}>
        <p className={cn(
          "text-sm leading-relaxed",
          isUser ? "text-primary-foreground" : "text-foreground"
        )}>
          {message.content}
        </p>
        <p className={cn(
          "text-xs mt-2 opacity-70",
          isUser ? "text-primary-foreground" : "text-muted-foreground"
        )}>
          {message.timestamp.toLocaleTimeString()}
        </p>
      </div>

      {isUser && (
        <div className="w-8 h-8 bg-secondary rounded-full flex items-center justify-center flex-shrink-0">
          <span className="text-secondary-foreground font-bold text-xs">U</span>
        </div>
      )}
    </div>
  );
};