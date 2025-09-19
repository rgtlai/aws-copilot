type Origin = {
  protocol: string;
  host: string;
};

const getDefaultOrigin = (): Origin => {
  if (typeof window === "undefined") {
    return { protocol: "http:", host: "localhost" };
  }
  return { protocol: window.location.protocol, host: window.location.host };
};

export const computeAgentWebSocketUrl = (
  options: { explicitUrl?: string; origin?: Origin } = {}
): string => {
  const { explicitUrl, origin } = options;
  if (explicitUrl) {
    return explicitUrl;
  }

  const { protocol, host } = origin ?? getDefaultOrigin();
  const wsProtocol = protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${host}/ws/agent`;
};

export const buildAgentWebSocketUrl = (): string =>
  computeAgentWebSocketUrl({
    explicitUrl: import.meta.env.VITE_AGENT_WS_URL,
    origin: getDefaultOrigin(),
  });
