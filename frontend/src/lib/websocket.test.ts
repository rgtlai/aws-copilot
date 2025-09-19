import { describe, expect, it, afterEach, vi } from 'vitest';
import { buildAgentWebSocketUrl, computeAgentWebSocketUrl } from './websocket';

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('buildAgentWebSocketUrl', () => {
  it('returns explicit URL when provided', () => {
    vi.stubEnv('VITE_AGENT_WS_URL', 'wss://custom.example/ws/agent');
    expect(buildAgentWebSocketUrl()).toBe('wss://custom.example/ws/agent');
  });
});

describe('computeAgentWebSocketUrl', () => {
  it('builds ws url from http origin', () => {
    expect(
      computeAgentWebSocketUrl({ origin: { protocol: 'http:', host: 'localhost:8080' } })
    ).toBe('ws://localhost:8080/ws/agent');
  });

  it('builds secure url from https origin', () => {
    expect(
      computeAgentWebSocketUrl({ origin: { protocol: 'https:', host: 'app.example.com' } })
    ).toBe('wss://app.example.com/ws/agent');
  });

  it('falls back to relative origin when not provided', () => {
    const result = computeAgentWebSocketUrl();
    expect(result.endsWith('/ws/agent')).toBe(true);
  });
});
