import React, { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import useWebSocket from 'react-use-websocket';
import type { SketchLogState } from '../types';
import { SketchLogContext } from './SketchLogContext';

export interface SketchLogProviderProps {
  url: string;
  children: ReactNode;
}

export const SketchLogProvider: React.FC<SketchLogProviderProps> = ({ url, children }) => {
  const [state, setState] = useState<SketchLogState | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const { lastJsonMessage, readyState } = useWebSocket<
    SketchLogState | { error: string }
  >(url, {
    shouldReconnect: () => true,
    reconnectAttempts: 10,
    reconnectInterval: 3000,
    onError: () => setError(new Error('WebSocket connection error')),
  });

  useEffect(() => {
    if (!lastJsonMessage) return;
    const update = window.setTimeout(() => {
      if ('error' in lastJsonMessage) {
        setError(new Error(lastJsonMessage.error));
        setState(null);
      } else {
        setState(lastJsonMessage);
        setError(null);
      }
    }, 0);
    return () => window.clearTimeout(update);
  }, [lastJsonMessage]);

  const isConnected = readyState === 1; // WebSocket.OPEN

  return (
    <SketchLogContext.Provider value={{ state, isConnected, error }}>
      {children}
    </SketchLogContext.Provider>
  );
};
