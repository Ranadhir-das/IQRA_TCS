import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { Appearance } from 'react-native';
import { Palettes, type AppColors, type ThemeMode } from '../constants/theme.ts';
import { getStoredThemeMode, setStoredThemeMode } from '../services/secureStorage.ts';

interface AppThemeContextValue {
  mode: ThemeMode;
  colors: AppColors;
  ready: boolean;
  toggleTheme: () => void;
}

const ThemeContext = createContext<AppThemeContextValue | null>(null);

export function AppThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>('light');
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let mounted = true;
    getStoredThemeMode()
      .then((savedMode) => {
        if (mounted && savedMode) {
          setMode(savedMode);
        }
      })
      .finally(() => {
        if (mounted) {
          setReady(true);
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!ready) return;
    try {
      Appearance.setColorScheme(mode);
    } catch {
      // Ignore on platforms where Appearance.setColorScheme is unsupported.
    }
  }, [mode, ready]);

  const value = useMemo<AppThemeContextValue>(
    () => ({
      mode,
      colors: Palettes[mode],
      ready,
      toggleTheme: () => {
        const nextMode: ThemeMode = mode === 'light' ? 'dark' : 'light';
        setMode(nextMode);
        void setStoredThemeMode(nextMode).catch(() => {});
      },
    }),
    [mode, ready]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useAppTheme(): AppThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useAppTheme must be used within an AppThemeProvider');
  }
  return context;
}

export function useAppStyles<T>(factory: (colors: AppColors) => T): T {
  const { colors } = useAppTheme();
  return useMemo(() => factory(colors), [colors, factory]);
}
