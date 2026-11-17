import React, { useRef, useState } from 'react';
import { router } from 'expo-router';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { BrandLogo } from '../components/BrandLogo.tsx';
import { ErrorBanner } from '../components/ErrorBanner.tsx';
import { KeyboardAwareContainer } from '../components/KeyboardAwareContainer.tsx';
import { PasswordInput } from '../components/PasswordInput.tsx';
import { APP_NAME, APP_SUBTITLE } from '../constants/config.ts';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../context/AppThemeContext.tsx';
import { useAuth } from '../context/AuthContext.tsx';
import { ApiError } from '../services/api.ts';
import { validateLoginForm, type LoginFormErrors } from '../utils/validation.ts';

export default function LoginScreen() {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();
  const { signIn, sessionNotice, clearSessionNotice } = useAuth();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<LoginFormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const passwordInputRef = useRef<TextInput>(null);

  const handleSignIn = async () => {
    if (submitting) return;

    setSubmitError(null);
    clearSessionNotice();

    const validationErrors = validateLoginForm(username, password);
    if (validationErrors.username || validationErrors.password) {
      setFieldErrors(validationErrors);
      return;
    }

    setFieldErrors({});
    setSubmitting(true);

    try {
      await signIn(username.trim(), password);
      setPassword('');
      router.replace('/(tabs)');
    } catch (error) {
      if (error instanceof ApiError) {
        const nextFieldErrors: LoginFormErrors = {};
        if (error.fieldErrors.username?.length) {
          nextFieldErrors.username = error.fieldErrors.username[0];
        }
        if (error.fieldErrors.password?.length) {
          nextFieldErrors.password = error.fieldErrors.password[0];
        }
        setFieldErrors(nextFieldErrors);
        setSubmitError(error.message);
      } else if (error instanceof Error) {
        setSubmitError(error.message);
      } else {
        setSubmitError('Unable to sign in right now. Please try again.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAwareContainer contentContainerStyle={styles.scrollContent}>
        <View style={styles.headerSection}>
          <BrandLogo
            size={88}
            accessibilityLabel="IQRA TCS official logo"
            style={styles.logoBadge}
          />
          <Text style={styles.brandTitle}>{APP_NAME}</Text>
          <Text style={styles.brandSubtitle}>{APP_SUBTITLE}</Text>
          <View style={styles.rolePill}>
            <Text style={styles.rolePillText}>EMPLOYEE SIGN IN</Text>
          </View>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Sign in to your account</Text>
          <Text style={styles.cardDescription}>
            Use your employee username and password issued by your IQRA TCS administrator.
          </Text>

          {sessionNotice ? (
            <View style={styles.bannerWrap}>
              <ErrorBanner
                message={sessionNotice}
                variant="info"
                onDismiss={clearSessionNotice}
              />
            </View>
          ) : null}

          {submitError ? (
            <View style={styles.bannerWrap}>
              <ErrorBanner
                message={submitError}
                variant="error"
                onDismiss={() => setSubmitError(null)}
              />
            </View>
          ) : null}

          <View style={styles.fieldGroup}>
            <Text style={styles.fieldLabel}>Username</Text>
            <TextInput
              value={username}
              onChangeText={(value) => {
                setUsername(value);
                if (fieldErrors.username) {
                  setFieldErrors((prev) => ({ ...prev, username: undefined }));
                }
              }}
              placeholder="Enter your username"
              placeholderTextColor={colors.placeholder}
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="username"
              textContentType="username"
              returnKeyType="next"
              editable={!submitting}
              onSubmitEditing={() => passwordInputRef.current?.focus()}
              style={[
                styles.input,
                fieldErrors.username ? styles.inputError : null,
                submitting ? styles.inputDisabled : null,
              ]}
            />
            {fieldErrors.username ? (
              <Text style={styles.fieldErrorText}>{fieldErrors.username}</Text>
            ) : null}
          </View>

          <View style={styles.fieldGroup}>
            <Text style={styles.fieldLabel}>Password</Text>
            <PasswordInput
              value={password}
              onChangeText={(value) => {
                setPassword(value);
                if (fieldErrors.password) {
                  setFieldErrors((prev) => ({ ...prev, password: undefined }));
                }
              }}
              placeholder="Enter your password"
              placeholderTextColor={colors.placeholder}
              autoComplete="password"
              textContentType="password"
              returnKeyType="go"
              editable={!submitting}
              hasError={Boolean(fieldErrors.password)}
              onSubmitEditing={() => void handleSignIn()}
            />
            {fieldErrors.password ? (
              <Text style={styles.fieldErrorText}>{fieldErrors.password}</Text>
            ) : null}
          </View>

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Sign In"
            accessibilityState={{ disabled: submitting, busy: submitting }}
            disabled={submitting}
            onPress={() => void handleSignIn()}
            style={({ pressed }) => [
              styles.submitButton,
              submitting && styles.submitButtonDisabled,
              pressed && !submitting && styles.submitButtonPressed,
            ]}
          >
            {submitting ? (
              <View style={styles.submittingRow}>
                <ActivityIndicator size="small" color={colors.onPrimary} />
                <Text style={styles.submitButtonText}>Signing in…</Text>
              </View>
            ) : (
              <Text style={styles.submitButtonText}>Sign In</Text>
            )}
          </Pressable>
        </View>

        <View style={styles.footerSection}>
          <Text style={styles.footerHelpText}>
            Need an account or password reset? Contact your IQRA TCS administrator.
          </Text>
        </View>
      </KeyboardAwareContainer>
    </SafeAreaView>
  );
}

const createStyles = (colors: AppColors) =>
  StyleSheet.create({
    safeArea: {
      flex: 1,
      backgroundColor: colors.background,
    },
    scrollContent: {
      flexGrow: 1,
      justifyContent: 'center',
      paddingHorizontal: 20,
      paddingVertical: 28,
    },
    headerSection: {
      alignItems: 'center',
      marginBottom: 24,
    },
    logoBadge: {
      marginBottom: 12,
    },
    brandTitle: {
      fontSize: 26,
      fontWeight: '800',
      color: colors.text,
      letterSpacing: 0.4,
    },
    brandSubtitle: {
      fontSize: 14,
      color: colors.muted,
      marginTop: 4,
    },
    rolePill: {
      marginTop: 10,
      paddingHorizontal: 12,
      paddingVertical: 4,
      borderRadius: 999,
      backgroundColor: colors.accentSoft,
    },
    rolePillText: {
      fontSize: 11,
      fontWeight: '800',
      color: colors.primary,
      letterSpacing: 0.8,
    },
    card: {
      backgroundColor: colors.surface,
      borderRadius: 18,
      padding: 20,
      borderWidth: 1,
      borderColor: colors.border,
    },
    cardTitle: {
      fontSize: 19,
      fontWeight: '700',
      color: colors.text,
    },
    cardDescription: {
      fontSize: 13.5,
      lineHeight: 19,
      color: colors.muted,
      marginTop: 6,
      marginBottom: 16,
    },
    bannerWrap: {
      marginBottom: 14,
    },
    fieldGroup: {
      marginBottom: 16,
    },
    fieldLabel: {
      fontSize: 13.5,
      fontWeight: '600',
      color: colors.secondary,
      marginBottom: 6,
    },
    input: {
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      paddingHorizontal: 14,
      paddingVertical: 13,
      fontSize: 15,
      color: colors.text,
    },
    inputError: {
      borderColor: colors.danger,
    },
    inputDisabled: {
      opacity: 0.6,
      backgroundColor: colors.surfaceMuted,
    },
    fieldErrorText: {
      marginTop: 5,
      fontSize: 12.5,
      color: colors.danger,
      fontWeight: '500',
    },
    submitButton: {
      marginTop: 6,
      backgroundColor: colors.primary,
      borderRadius: 12,
      paddingVertical: 14,
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: 48,
    },
    submitButtonDisabled: {
      opacity: 0.7,
    },
    submitButtonPressed: {
      opacity: 0.88,
    },
    submittingRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
    },
    submitButtonText: {
      color: colors.onPrimary,
      fontSize: 15.5,
      fontWeight: '700',
    },
    footerSection: {
      marginTop: 20,
      alignItems: 'center',
      paddingHorizontal: 12,
    },
    footerHelpText: {
      fontSize: 12.5,
      lineHeight: 18,
      color: colors.muted,
      textAlign: 'center',
    },
  });
