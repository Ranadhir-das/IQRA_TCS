import React, { useEffect } from 'react';
import {
  Stack,
  router,
  usePathname,
} from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { BrandLogo } from '../components/BrandLogo.tsx';
import { APP_NAME, APP_SUBTITLE } from '../constants/config.ts';
import { AppThemeProvider, useAppTheme } from '../context/AppThemeContext.tsx';
import { AuthProvider, useAuth } from '../context/AuthContext.tsx';

void SplashScreen.preventAutoHideAsync().catch(() => {});

function AuthRouteGuard() {
  const { status, user } = useAuth();
  const pathname = usePathname();

  useEffect(() => {
    if (status === 'loading') {
      return;
    }

    const isEmployeeAuthenticated =
      status === 'authenticated' &&
      Boolean(user) &&
      user?.role === 'EMPLOYEE' &&
      user?.is_active === true;

    if (!isEmployeeAuthenticated) {
      if (pathname !== '/login') {
        router.replace('/login');
      }
      return;
    }

    if (pathname === '/login') {
      router.replace('/(tabs)');
    }
  }, [status, user, pathname]);

  return null;
}

function RootNavigator() {
  const { colors, ready } = useAppTheme();
  const { status, user } = useAuth();

  useEffect(() => {
    if (ready && status !== 'loading') {
      void SplashScreen.hideAsync().catch(() => {});
    }
  }, [ready, status]);

  if (!ready || status === 'loading') {
    return (
      <View style={[styles.splashContainer, { backgroundColor: colors.background }]}>
        <BrandLogo
          size={96}
          accessibilityLabel="IQRA TCS official logo"
          style={styles.brandLogoSpacing}
        />
        <Text style={[styles.splashTitle, { color: colors.text }]}>{APP_NAME}</Text>
        <Text style={[styles.splashSubtitle, { color: colors.muted }]}>
          {APP_SUBTITLE}
        </Text>
        <ActivityIndicator
          size="large"
          color={colors.primary}
          style={styles.splashSpinner}
        />
      </View>
    );
  }

  const isEmployeeAuthenticated =
    status === 'authenticated' &&
    Boolean(user) &&
    user?.role === 'EMPLOYEE' &&
    user?.is_active === true;

  return (
    <>
      <AuthRouteGuard />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: colors.background },
          animation: 'slide_from_right',
        }}
      >
        <Stack.Screen name="login" />
        <Stack.Protected guard={isEmployeeAuthenticated}>
          <Stack.Screen name="(tabs)" />
          <Stack.Screen name="lead-details" />
          <Stack.Screen name="log-call" />
          <Stack.Screen name="schedule-followup" />
          <Stack.Screen name="change-password" />
        </Stack.Protected>
      </Stack>
    </>
  );
}

function ThemedApp() {
  const { mode } = useAppTheme();

  return (
    <>
      <StatusBar style={mode === 'dark' ? 'light' : 'dark'} />
      <AuthProvider>
        <RootNavigator />
      </AuthProvider>
    </>
  );
}

export default function RootLayout() {
  return (
    <AppThemeProvider>
      <ThemedApp />
    </AppThemeProvider>
  );
}

const styles = StyleSheet.create({
  splashContainer: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  brandLogoSpacing: {
    marginBottom: 16,
  },
  splashTitle: {
    fontSize: 24,
    fontWeight: '800',
    letterSpacing: 0.4,
  },
  splashSubtitle: {
    fontSize: 14,
    marginTop: 4,
  },
  splashSpinner: {
    marginTop: 24,
  },
});
