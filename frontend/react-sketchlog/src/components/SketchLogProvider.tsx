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

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let reconnectAttempts = 0;
    let stopped = false;

    const connect = () => {
      socket = new WebSocket(url);
      socket.onopen = () => {
        reconnectAttempts = 0;
        setIsConnected(true);
        setError(null);
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(String(event.data)) as SketchLogState | { error: string };
          if ('error' in message) {
            setError(new Error(message.error));
            setState(null);
          } else {
            setState(message);
            setError(null);
          }
        } catch {
          setError(new Error('Invalid WebSocket message'));
        }
      };
      socket.onerror = () => setError(new Error('WebSocket connection error'));
      socket.onclose = () => {
        setIsConnected(false);
        if (!stopped && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
          reconnectAttempts += 1;
          reconnectTimer = window.setTimeout(connect, RECONNECT_INTERVAL_MS);
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [url]);

  return (
    <SketchLogContext.Provider value={{ state, isConnected, error }}>
      {children}
    </SketchLogContext.Provider>
  );
};
