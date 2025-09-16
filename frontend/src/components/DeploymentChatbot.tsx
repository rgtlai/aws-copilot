import React, { useState } from 'react';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';
import { DeployButton } from './DeployButton';
import { StatusIndicator } from './StatusIndicator';

export interface Message {
  id: string;
  content: string;
  sender: 'user' | 'assistant';
  timestamp: Date;
}

const DeploymentChatbot = () => {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      content: 'Hello! I\'m your AWS deployment assistant. I\'ll help you deploy your application to AWS. What would you like to deploy today?',
      sender: 'assistant',
      timestamp: new Date()
    }
  ]);
  const [isReadyToDeploy, setIsReadyToDeploy] = useState(false);
  const [deploymentStatus, setDeploymentStatus] = useState<'idle' | 'deploying' | 'success' | 'error'>('idle');
  const [errorMessage, setErrorMessage] = useState('');

  const handleSendMessage = (content: string) => {
    const userMessage: Message = {
      id: Date.now().toString(),
      content,
      sender: 'user',
      timestamp: new Date()
    };

    setMessages(prev => [...prev, userMessage]);

    // Mock LLM response logic
    setTimeout(() => {
      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        content: generateMockResponse(content, messages.length),
        sender: 'assistant',
        timestamp: new Date()
      };

      setMessages(prev => [...prev, assistantMessage]);
      
      // Check if ready to deploy based on conversation
      if (messages.length >= 6) {
        setIsReadyToDeploy(true);
      }
    }, 1000);
  };

  const generateMockResponse = (userInput: string, messageCount: number): string => {
    const responses = [
      "Great! What type of application are you looking to deploy? (e.g., web app, API, Lambda function)",
      "Perfect! Which AWS region would you prefer for your deployment?",
      "Excellent choice! Do you need any specific AWS services like RDS, S3, or CloudFront?",
      "Got it! What's your preferred instance type or compute configuration?",
      "Wonderful! I have all the information needed. Your deployment configuration looks good!"
    ];
    
    if (messageCount < responses.length) {
      return responses[messageCount - 1];
    }
    
    return "I'm ready to help you deploy! Click the deploy button when you're ready to proceed.";
  };

  const handleDeploy = async () => {
    setDeploymentStatus('deploying');
    setErrorMessage('');
    
    // Simulate deployment process
    setTimeout(() => {
      // Simulate random success/failure for demo
      const isSuccess = Math.random() > 0.3;
      
      if (isSuccess) {
        setDeploymentStatus('success');
        const successMessage: Message = {
          id: Date.now().toString(),
          content: '🎉 Deployment successful! Your application is now live at: https://your-app.aws-region.amazonaws.com',
          sender: 'assistant',
          timestamp: new Date()
        };
        setMessages(prev => [...prev, successMessage]);
      } else {
        setDeploymentStatus('error');
        setErrorMessage('IAM permissions error: User does not have required permissions for EC2 instance creation');
        const errorMessage: Message = {
          id: Date.now().toString(),
          content: 'Deployment failed due to IAM permissions. Let me help you resolve this. You need to add the EC2FullAccess policy to your IAM user. Would you like me to guide you through this?',
          sender: 'assistant',
          timestamp: new Date()
        };
        setMessages(prev => [...prev, errorMessage]);
      }
    }, 3000);
  };

  return (
    <div className="flex flex-col h-screen bg-gradient-background">
      {/* Header */}
      <div className="border-b border-border bg-card/50 backdrop-blur-sm">
        <div className="container mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 bg-gradient-primary rounded-lg flex items-center justify-center">
                <span className="text-primary-foreground font-bold text-sm">AWS</span>
              </div>
              <div>
                <h1 className="text-xl font-bold font-mono">AWS Deploy Assistant</h1>
                <p className="text-sm text-muted-foreground">AI-powered deployment companion</p>
              </div>
            </div>
            <StatusIndicator status={deploymentStatus} />
          </div>
        </div>
      </div>

      {/* Chat Messages */}
      <div className="flex-1 overflow-hidden">
        <div className="h-full overflow-y-auto p-6 space-y-4">
          <div className="max-w-4xl mx-auto space-y-4">
            {messages.map((message) => (
              <ChatMessage key={message.id} message={message} />
            ))}
          </div>
        </div>
      </div>

      {/* Error Display */}
      {deploymentStatus === 'error' && errorMessage && (
        <div className="border-t border-destructive/20 bg-destructive/10 p-4">
          <div className="max-w-4xl mx-auto">
            <div className="flex items-start gap-3">
              <div className="w-6 h-6 rounded-full bg-destructive flex items-center justify-center flex-shrink-0 mt-1">
                <span className="text-destructive-foreground text-xs">!</span>
              </div>
              <div>
                <h3 className="font-semibold text-destructive mb-1">Deployment Error</h3>
                <p className="text-sm text-destructive/90 font-mono">{errorMessage}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Input Area */}
      <div className="border-t border-border bg-card/30 backdrop-blur-sm">
        <div className="max-w-4xl mx-auto p-6">
          <div className="flex gap-4 items-end">
            <div className="flex-1">
              <ChatInput onSendMessage={handleSendMessage} disabled={deploymentStatus === 'deploying'} />
            </div>
            {isReadyToDeploy && (
              <DeployButton 
                onClick={handleDeploy} 
                status={deploymentStatus}
                disabled={deploymentStatus === 'deploying'}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default DeploymentChatbot;