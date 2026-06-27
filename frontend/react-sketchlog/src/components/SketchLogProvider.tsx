import React, { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import useWebSocket from 'react-use-websocket';
import type { SketchLogState, SketchLogContextType } from '../types';

const SketchLogContext = createContext<SketchLogContextType>({
  state: null,
  isConnected: false,
  error: null,
});

export const useSketchLog = () => useContext(SketchLogContext);

export interface SketchLogProviderProps {
  url: string;
  children: ReactNode;
}

export const SketchLogProvider: React.FC<SketchLogProviderProps> = ({ url, children }) => {
  const [state, setState] = useState<SketchLogState | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const { lastJsonMessage, readyState } = useWebSocket<SketchLogState>(url, {
    shouldReconnect: () => true,
    reconnectAttempts: 10,
    reconnectInterval: 3000,
    onError: () => setError(new Error('WebSocket connection error')),
  });

  useEffect(() => {
    if (lastJsonMessage) {
      if ('error' in lastJsonMessage) {
        setError(new Error((lastJsonMessage as any).error));
      } else {
        setState(lastJsonMessage);
        setError(null);
      }
    }
  }, [lastJsonMessage]);

  const isConnected = readyState === 1; // WebSocket.OPEN

  return (
    <SketchLogContext.Provider value={{ state, isConnected, error }}>
      {children}
    </SketchLogContext.Provider>
  );
};
