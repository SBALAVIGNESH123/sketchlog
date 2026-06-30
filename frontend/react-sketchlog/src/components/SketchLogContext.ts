import { createContext, useContext } from 'react';
import type { SketchLogContextType } from '../types';

export const SketchLogContext = createContext<SketchLogContextType>({
  state: null,
  isConnected: false,
  error: null,
});

export const useSketchLog = () => useContext(SketchLogContext);
