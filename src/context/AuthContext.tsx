import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { setSessionInvalidatedListener } from '../services/api.ts';
import {
  changeEmployeePassword,
   fetchCurrentUser,
  loginEmployee,
  logoutEmployee,
  restoreEmployeeSession,
} from '../services/auth.ts';
import type { ChangePasswordRequest, UserProfile } from '../types/index.ts';

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated';

interface AuthContextValue {
  status: AuthStatus;
  loading: boolean;
  user: UserProfile | null;
  sessionNotice: string | null;
  clearSessionNotice: () => void;
  signIn: (username: string, password: string) => Promise<UserProfile>;
  signOut: (notice?: string) => Promise<void>;
  refreshProfile: () => Promise<UserProfile | null>;
  changePassword: (payload: ChangePasswordRequest) => Promise<string>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<UserProfile | null>(null);
  const [sessionNotice, setSessionNotice] = useState<string | null>(null);

  useEffect(() => {
    setSessionInvalidatedListener((reason: string) => {
      setUser(null);
      setStatus('unauthenticated');
      setSessionNotice(reason || 'Your session has expired. Please sign in again.');
    });
    return () => {
      setSessionInvalidatedListener(null);
    };
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const restoredUser = await restoreEmployeeSession();
        if (!active) return;
        if (restoredUser) {
          setUser(restoredUser);
          setStatus('authenticated');
        } else {
          setUser(null);
          setStatus('unauthenticated');
        }
      } catch {
        if (!active) return;
        setUser(null);
        setStatus('unauthenticated');
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const clearSessionNotice = useCallback(() => {
    setSessionNotice(null);
  }, []);

  const signIn = useCallback(async (username: string, password: string): Promise<UserProfile> => {
    setSessionNotice(null);
    const profile = await loginEmployee(username, password);
    setUser(profile);
    setStatus('authenticated');
    return profile;
  }, []);

  const signOut = useCallback(async (notice?: string): Promise<void> => {
    try {
      await logoutEmployee();
    } finally {
      setUser(null);
      setStatus('unauthenticated');
      if (notice) {
        setSessionNotice(notice);
      }
    }
  }, []);

  const refreshProfile = useCallback(async (): Promise<UserProfile | null> => {
    try {
      const updated = await fetchCurrentUser();
      setUser(updated);
      setStatus('authenticated');
      return updated;
    } catch {
      return null;
    }
  }, []);

  const changePassword = useCallback(
    async (payload: ChangePasswordRequest): Promise<string> => {
      const message = await changeEmployeePassword(payload);
      setUser(null);
      setStatus('unauthenticated');
      setSessionNotice(`${message} Please sign in with your new password.`);
      return message;
    },
    []
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      loading: status === 'loading',
      user,
      sessionNotice,
      clearSessionNotice,
      signIn,
      signOut,
      refreshProfile,
      changePassword,
    }),
    [
      status,
      user,
      sessionNotice,
      clearSessionNotice,
      signIn,
      signOut,
      refreshProfile,
      changePassword,
    ]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
