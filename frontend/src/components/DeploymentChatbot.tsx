import React, { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';
import { DeployButton } from './DeployButton';
import { StatusIndicator } from './StatusIndicator';
import { buildAgentWebSocketUrl } from '@/lib/websocket';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/components/ui/use-toast';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';

export interface Message {
  id: string;
  content: string;
  sender: 'user' | 'assistant';
  timestamp: Date;
  thoughts?: string[];
}

interface CredentialsStatusPayload {
  status: 'missing' | 'present';
  updated_at?: string;
  access_key_last_four?: string;
}

const DeploymentChatbot = () => {
  const createMessage = (content: string, sender: Message['sender'], thoughts?: string[]): Message => ({
    id: globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2),
    content,
    sender,
    timestamp: new Date(),
    thoughts: thoughts && thoughts.length ? thoughts : undefined,
  });

  const [messages, setMessages] = useState<Message[]>([
    createMessage(
      "Hello! I'm your AWS deployment assistant. Before I can deploy anything, you'll need to securely provide your AWS credentials using the button below. I'm happy to answer questions in the meantime!",
      'assistant'
    )
  ]);
  const [deploymentStatus, setDeploymentStatus] = useState<'idle' | 'deploying' | 'success' | 'error'>('idle');
  const [errorMessage, setErrorMessage] = useState('');
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'closed' | 'error'>('connecting');
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const closedByClientRef = useRef(false);
  const [agentReady, setAgentReady] = useState(false);
  const [hasCredentials, setHasCredentials] = useState(false);
  const [credentialsDialogOpen, setCredentialsDialogOpen] = useState(false);
  const [savingCredentials, setSavingCredentials] = useState(false);
  const [accessKeyId, setAccessKeyId] = useState('');
  const [secretAccessKey, setSecretAccessKey] = useState('');
  const [sessionToken, setSessionToken] = useState('');
  const [credentialsStatus, setCredentialsStatus] = useState<CredentialsStatusPayload | null>(null);
  const [uploadPrompt, setUploadPrompt] = useState<string | null>(null);
  const [selectedUploadFile, setSelectedUploadFile] = useState<File | null>(null);
  const [uploadBucket, setUploadBucket] = useState('');
  const [uploadObjectKey, setUploadObjectKey] = useState('');
  const [uploadRegion, setUploadRegion] = useState('us-east-1');
  const [uploadingFile, setUploadingFile] = useState(false);
  const [bucketHint, setBucketHint] = useState('');
  const pingIntervalRef = useRef<number | null>(null);
  const { toast } = useToast();

  const readyToDeploy = useMemo(() => agentReady && hasCredentials, [agentReady, hasCredentials]);

  const loadCredentialsStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/credentials/aws');
      if (!response.ok) {
        throw new Error('Unable to load credentials status.');
      }
      const data: CredentialsStatusPayload = await response.json();
      setCredentialsStatus(data);
      setHasCredentials(data.status === 'present');
    } catch (error) {
      console.error('Failed to load credentials status', error);
    }
  }, []);

  useEffect(() => {
    loadCredentialsStatus();
  }, [loadCredentialsStatus]);

  useEffect(() => {
    if (credentialsDialogOpen) {
      loadCredentialsStatus();
    }
  }, [credentialsDialogOpen, loadCredentialsStatus]);

  const sanitizeBucketCandidate = useCallback((value: string): string => {
    return value.trim().replace(/[.,;:]+$/g, '');
  }, []);

  const inferBucketFromText = useCallback((text: string): string | null => {
    if (!text) {
      return null;
    }

    const patterns = [
      /bucket\s+`([^`]+)`/i,
      /bucket\s+"([^"]+)"/i,
      /bucket\s+'([^']+)'/i,
      /bucket(?:\s+name)?\s+(?:is\s+)?([a-z0-9.-]{3,63})/i,
    ];

    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (match && match[1]) {
        const candidate = sanitizeBucketCandidate(match[1]);
        if (candidate) {
          return candidate;
        }
      }
    }

    return null;
  }, [sanitizeBucketCandidate]);

  const formattedCredentialsUpdatedAt = useMemo(() => {
    if (!credentialsStatus?.updated_at) {
      return null;
    }

    const parsed = new Date(credentialsStatus.updated_at);
    if (Number.isNaN(parsed.getTime())) {
      return credentialsStatus.updated_at;
    }

    return parsed.toLocaleString();
  }, [credentialsStatus]);

  useEffect(() => {
    const wsUrl = buildAgentWebSocketUrl();
    const socket = new WebSocket(wsUrl);
    socketRef.current = socket;
    setConnectionStatus('connecting');
    setConnectionError(null);
    closedByClientRef.current = false;

    socket.onopen = () => {
      setConnectionStatus('connected');
      setConnectionError(null);
      if (pingIntervalRef.current !== null) {
        window.clearInterval(pingIntervalRef.current);
      }
      pingIntervalRef.current = window.setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'ping' }));
        } else if (pingIntervalRef.current !== null) {
          window.clearInterval(pingIntervalRef.current);
          pingIntervalRef.current = null;
        }
      }, 25000);
    };

    socket.onerror = () => {
      setConnectionStatus('error');
      setConnectionError('Unable to reach the deployment agent.');
    };

    socket.onclose = (event) => {
      socketRef.current = null;
      if (pingIntervalRef.current !== null) {
        window.clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = null;
      }
      setConnectionStatus('closed');
      if (!event.wasClean && !closedByClientRef.current) {
        setConnectionError('Agent connection closed unexpectedly.');
      }
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === 'pong') {
          return;
        }

        if (data.type === 'agent_response') {
          const finalAnswer: string | undefined = data.final_answer;
          const reasoningSteps = Array.isArray(data.thought_process)
            ? data.thought_process
                .map((step: any, index: number) => {
                  const parts: string[] = [];
                  if (step.thought) {
                    parts.push(`Thought ${index + 1}: ${step.thought}`);
                  }
                  let observation: string | null = null;
                  const rawObservation =
                    typeof step.observation === 'string'
                      ? step.observation
                      : step.observation?.result ?? step.observation;
                  if (rawObservation !== undefined && rawObservation !== null) {
                    observation =
                      typeof rawObservation === 'string'
                        ? rawObservation
                        : (() => {
                            try {
                              return JSON.stringify(rawObservation);
                            } catch {
                              return String(rawObservation);
                            }
                          })();
                  }
                  if (observation) {
                    parts.push(`Observation: ${observation}`);
                  }
                  return parts.join('\n');
                })
                .filter(Boolean)
            : [];

          let assistantMessage = '';
          const finalReasoning: string[] = [];
          let uploadInstructions: string | null = null;

          if (typeof finalAnswer === 'string' && finalAnswer.trim()) {
            const lines = finalAnswer
              .split(/\n+/)
              .map((line) => line.trim())
              .filter(Boolean);

            const messageLines: string[] = [];

            lines.forEach((line) => {
              if (/^Thought\s*\d*:/.test(line) || line.startsWith('Observation:')) {
                finalReasoning.push(line);
                return;
              }
              if (line.startsWith('UPLOAD_PROMPT:')) {
                uploadInstructions = line.replace('UPLOAD_PROMPT:', '').trim();
                return;
              }
              messageLines.push(line);
            });

            assistantMessage = messageLines.join('\n').trim();
          }

          let combinedThoughts = [...reasoningSteps, ...finalReasoning].filter((value, index, array) => value && array.indexOf(value) === index);

          let uploadInstructionsFromThoughts: string | null = null;
          combinedThoughts = combinedThoughts.reduce<string[]>((acc, entry) => {
            if (entry.includes('UPLOAD_PROMPT:')) {
              const [, instructionPart] = entry.split('UPLOAD_PROMPT:');
              const instructions = (instructionPart ?? '').trim();
              uploadInstructionsFromThoughts = instructions || uploadInstructionsFromThoughts;
              return acc;
            }
            return [...acc, entry];
          }, []);

          const effectiveInstructions = uploadInstructions ?? uploadInstructionsFromThoughts;
          if (effectiveInstructions !== null) {
              const resolvedInstructions = effectiveInstructions || 'Please choose a file to upload.';
              setUploadPrompt(resolvedInstructions);
              if (!uploadBucket.trim()) {
                const inferredBucket = inferBucketFromText(resolvedInstructions);
                if (inferredBucket) {
                  setUploadBucket(inferredBucket);
                  setBucketHint(inferredBucket);
                }
              }
            if (!assistantMessage) {
              assistantMessage = resolvedInstructions;
            }
          }

          const iterationLimitMessage = assistantMessage.startsWith('❌ Stopped after reaching maximum iterations limit');
          if (iterationLimitMessage) {
            const bucket = uploadBucket.trim() || bucketHint;
            if (bucket) {
              const fallbackInstructions = `Please upload the file you would like to add to the S3 bucket "${bucket}".`;
              setUploadPrompt(fallbackInstructions);
              setBucketHint(bucket);
              setUploadBucket((current) => current || bucket);
              assistantMessage = `${assistantMessage}\n\n${fallbackInstructions}`.trim();
            }
          }

          if (!assistantMessage) {
            assistantMessage = 'Agent responded without additional details.';
          }

          if (
            assistantMessage.toLowerCase().includes('waiting for file upload') &&
            !uploadPrompt
          ) {
            const bucketName = bucketHint || uploadBucket;
            const fallbackInstructions = bucketName
              ? `Please upload the file you would like to add to the S3 bucket "${sanitizeBucketCandidate(bucketName)}".`
              : 'Please upload the file you would like to add to the target S3 bucket.';
            setUploadPrompt(fallbackInstructions);
            if (bucketName) {
              const normalized = sanitizeBucketCandidate(bucketName);
              setUploadBucket((current) => current || normalized);
              setBucketHint((current) => current || normalized);
            }
          }

          setMessages(prev => [...prev, createMessage(assistantMessage, 'assistant', combinedThoughts)]);

          if (data.ready_to_deploy === true || (finalAnswer && finalAnswer.toLowerCase().includes('ready'))) {
            setAgentReady(true);
            if (!hasCredentials) {
              setMessages(prev => [
                ...prev,
                createMessage(
                  'Before we can proceed with deployment, please provide AWS credentials using the "Configure AWS Credentials" button below.',
                  'assistant'
                )
              ]);
            }
          }
        } else if (data.type === 'error') {
          const detail = typeof data.detail === 'string' ? data.detail : 'Agent reported an error.';
          setMessages(prev => [...prev, createMessage(`⚠️ ${detail}`, 'assistant')]);
        }
      } catch (error) {
        setMessages(prev => [
          ...prev,
          createMessage('Failed to parse response from agent.', 'assistant')
        ]);
      }
    };

    return () => {
      closedByClientRef.current = true;
      socketRef.current = null;
      if (pingIntervalRef.current !== null) {
        window.clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = null;
      }
      socket.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSendMessage = (content: string) => {
    const userMessage = createMessage(content, 'user');
    setMessages(prev => [...prev, userMessage]);

    const inferredBucket = inferBucketFromText(content);
    if (inferredBucket) {
      const normalized = sanitizeBucketCandidate(inferredBucket);
      setBucketHint(normalized);
      setUploadBucket((current) => current || normalized);
    }

    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ message: content }));
    } else {
      setMessages(prev => [
        ...prev,
        createMessage('Connection to the deployment agent is not ready yet. Please wait and try again.', 'assistant')
      ]);
    }
  };

  const handleCredentialsSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!accessKeyId.trim() || !secretAccessKey.trim()) {
      toast({ title: 'Missing fields', description: 'Access Key ID and Secret Access Key are required.', variant: 'destructive' });
      return;
    }

    setSavingCredentials(true);
    try {
      const response = await fetch('/api/credentials/aws', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          accessKeyId: accessKeyId.trim(),
          secretAccessKey: secretAccessKey.trim(),
          sessionToken: sessionToken.trim() || undefined,
        }),
      });

      if (!response.ok) {
        const detail = await response.json().catch(() => ({ detail: 'Unable to save credentials.' }));
        const message = typeof detail.detail === 'string' ? detail.detail : 'Unable to save credentials.';
        throw new Error(message);
      }

      setHasCredentials(true);
      await loadCredentialsStatus();
      setCredentialsDialogOpen(false);
      setAccessKeyId('');
      setSecretAccessKey('');
      setSessionToken('');
      toast({ title: 'Credentials saved', description: 'AWS credentials stored securely. You can deploy once the agent confirms readiness.' });

      if (agentReady) {
        setMessages(prev => [
          ...prev,
          createMessage('Great! Credentials are on file. You can deploy whenever you are ready.', 'assistant')
        ]);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to save credentials.';
      toast({ title: 'Save failed', description: message, variant: 'destructive' });
    } finally {
      setSavingCredentials(false);
    }
  };

  const handleUploadFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    setSelectedUploadFile(file ?? null);
    if (file) {
      setUploadObjectKey(prev => prev || file.name);
    }
  };

  const handleUploadSubmit = async (event: FormEvent) => {
    event.preventDefault();

    if (!selectedUploadFile) {
      toast({ title: 'No file selected', description: 'Choose a file before uploading.', variant: 'destructive' });
      return;
    }

    if (!uploadBucket.trim()) {
      toast({ title: 'Bucket required', description: 'Enter the target S3 bucket name.', variant: 'destructive' });
      return;
    }

    const formData = new FormData();
    formData.append('file', selectedUploadFile);
    formData.append('bucket', uploadBucket.trim());
    if (uploadObjectKey.trim()) {
      formData.append('objectKey', uploadObjectKey.trim());
    }
    if (uploadRegion.trim()) {
      formData.append('region', uploadRegion.trim());
    }

    setUploadingFile(true);
    try {
      const response = await fetch('/api/aws/upload', {
        method: 'POST',
        body: formData,
      });

      const payload = await response.json().catch(() => ({ detail: 'Upload failed' }));

      if (!response.ok) {
        const message = typeof payload.detail === 'string' ? payload.detail : 'Upload failed';
        throw new Error(message);
      }

      const result = payload.result ?? {};
      const effectiveBucket = result.bucket ?? uploadBucket;
      const effectiveObject = result.object ?? (uploadObjectKey || selectedUploadFile.name);
      toast({ title: 'Upload complete', description: `Uploaded ${effectiveObject} to ${effectiveBucket}.` });

      setUploadPrompt(null);
      setSelectedUploadFile(null);
      setUploadBucket('');
      setUploadObjectKey('');
      setUploadRegion('us-east-1');

      handleSendMessage(`File ${effectiveObject} uploaded to S3 bucket ${effectiveBucket}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Upload failed';
      toast({ title: 'Upload failed', description: message, variant: 'destructive' });
    } finally {
      setUploadingFile(false);
    }
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
                <p className="text-xs text-muted-foreground font-mono">
                  Agent connection: {connectionStatus}
                  {connectionError ? ` — ${connectionError}` : ''}
                </p>
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
              {uploadPrompt && (
                <form onSubmit={handleUploadSubmit} className="mb-4 space-y-3 rounded-lg border border-dashed border-muted-foreground/40 p-4">
                  <div>
                    <p className="text-sm font-medium">{uploadPrompt}</p>
                    <p className="text-xs text-muted-foreground">The file will be uploaded securely to the specified S3 bucket.</p>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="upload-bucket">Bucket Name</Label>
                      <Input
                        id="upload-bucket"
                        value={uploadBucket}
                        onChange={(event) => setUploadBucket(event.target.value)}
                        placeholder="my-s3-bucket"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="upload-region">Region</Label>
                      <Input
                        id="upload-region"
                        value={uploadRegion}
                        onChange={(event) => setUploadRegion(event.target.value)}
                        placeholder="us-east-1"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="upload-key">Object Key (optional)</Label>
                      <Input
                        id="upload-key"
                        value={uploadObjectKey}
                        onChange={(event) => setUploadObjectKey(event.target.value)}
                        placeholder="optional/key.txt"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="upload-file">File</Label>
                      <Input id="upload-file" type="file" onChange={handleUploadFileChange} required />
                      {selectedUploadFile && (
                        <p className="text-xs text-muted-foreground">Selected: {selectedUploadFile.name}</p>
                      )}
                    </div>
                  </div>
                  <div className="flex justify-end">
                    <Button type="submit" disabled={uploadingFile}>
                      {uploadingFile ? 'Uploading…' : 'Upload to S3'}
                    </Button>
                  </div>
                </form>
              )}
              <ChatInput
                onSendMessage={handleSendMessage}
                disabled={deploymentStatus === 'deploying' || connectionStatus !== 'connected'}
              />
            </div>
            <Dialog open={credentialsDialogOpen} onOpenChange={setCredentialsDialogOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" className="flex-shrink-0">Configure AWS Credentials</Button>
              </DialogTrigger>
              <DialogContent className="sm:max-w-md">
                <DialogHeader>
                  <DialogTitle>Configure AWS Credentials</DialogTitle>
                  <DialogDescription>
                    Provide credentials with permissions to deploy your application. They are stored securely in MongoDB.
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={handleCredentialsSubmit} className="space-y-4">
                  {credentialsStatus?.status === 'present' && (
                    <Alert className="bg-muted/30">
                      <AlertTitle>Credentials on file</AlertTitle>
                      <AlertDescription>
                        {credentialsStatus.access_key_last_four
                          ? `Access key ending in ${credentialsStatus.access_key_last_four} is currently stored.`
                          : 'AWS credentials are currently stored.'}
                        {formattedCredentialsUpdatedAt ? ` Last updated ${formattedCredentialsUpdatedAt}.` : ''}
                        {' '}Submit new values to replace the saved credentials.
                      </AlertDescription>
                    </Alert>
                  )}
                  <div className="space-y-2">
                    <Label htmlFor="aws-access-key">Access Key ID</Label>
                    <Input
                      id="aws-access-key"
                      value={accessKeyId}
                      onChange={(event) => setAccessKeyId(event.target.value)}
                      placeholder="AKIA..."
                      required
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="aws-secret-key">Secret Access Key</Label>
                    <Input
                      id="aws-secret-key"
                      type="password"
                      value={secretAccessKey}
                      onChange={(event) => setSecretAccessKey(event.target.value)}
                      required
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="aws-session-token">Session Token (optional)</Label>
                    <Input
                      id="aws-session-token"
                      type="password"
                      value={sessionToken}
                      onChange={(event) => setSessionToken(event.target.value)}
                      placeholder="If using temporary credentials"
                    />
                  </div>
                  <DialogFooter>
                    <Button type="submit" disabled={savingCredentials}>
                      {savingCredentials ? 'Saving…' : 'Save Credentials'}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
            {readyToDeploy && (
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
