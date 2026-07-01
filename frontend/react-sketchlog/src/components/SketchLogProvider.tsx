import React, { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { SketchLogState } from '../types';
import { SketchLogContext } from './SketchLogContext';

export interface SketchLogProviderProps {
  url: string;
  children: ReactNode;
}

const MAX_RECONNECT_ATTEMPTS = 10;
const RECONNECT_INTERVAL_MS = 3000;

export const SketchLogProvider: React.FC<SketchLogProviderProps> = ({ url, children }) => {
  const [state, setState] = useState<SketchLogState | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [connectionUrl, setConnectionUrl] = useState<string | null>(null);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let reconnectAttempts = 0;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      const nextSocket = new WebSocket(url);
      socket = nextSocket;
      const isCurrent = () => !stopped && socket === nextSocket;

      nextSocket.onopen = () => {
        if (!isCurrent()) return;
        reconnectAttempts = 0;
        setConnectionUrl(url);
        setIsConnected(true);
        setError(null);
      };
      nextSocket.onmessage = (event) => {
        if (!isCurrent()) return;
        try {
          const message = JSON.parse(String(event.data)) as SketchLogState | { error: string };
          if ('error' in message) {
            setConnectionUrl(url);
            setError(new Error(message.error));
            setState(null);
          } else {
            setConnectionUrl(url);
            setState(message);
            setError(null);
          }
        } catch {
          setError(new Error('Invalid WebSocket message'));
        }
      };
      nextSocket.onerror = () => {
        if (isCurrent()) {
          setConnectionUrl(url);
          setError(new Error('WebSocket connection error'));
        }
      };
      nextSocket.onclose = () => {
        if (!isCurrent()) return;
        socket = null;
        setConnectionUrl(url);
        setIsConnected(false);
        if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
          reconnectAttempts += 1;
          reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            connect();
          }, RECONNECT_INTERVAL_MS);
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      const currentSocket = socket;
      socket = null;
      currentSocket?.close();
    };
  }, [url]);

  const isCurrentUrl = connectionUrl === url;
  return (
    <SketchLogContext.Provider value={{
      state: isCurrentUrl ? state : null,
      isConnected: isCurrentUrl && isConnected,
      error: isCurrentUrl ? error : null,
    }}>
      {children}
    </SketchLogContext.Provider>
  );
};
