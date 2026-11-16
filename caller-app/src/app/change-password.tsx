import React, { useState } from 'react';
import { router } from 'expo-router';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { AnimatedBackButton } from '../components/AnimatedBackButton.tsx';
import { ErrorBanner } from '../components/ErrorBanner.tsx';
import { KeyboardAwareContainer } from '../components/KeyboardAwareContainer.tsx';
import { PasswordInput } from '../components/PasswordInput.tsx';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../context/AppThemeContext.tsx';
import { useAuth } from '../context/AuthContext.tsx';
import { ApiError } from '../services/api.ts';
import {
  validateChangePasswordForm,
  type ChangePasswordFormErrors,
} from '../utils/validation.ts';

export default function ChangePasswordScreen() {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();
  const { changePassword } = useAuth();

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<ChangePasswordFormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (submitting) return;
    setSubmitError(null);

    const validationErrors = validateChangePasswordForm(
      currentPassword,
      newPassword,
      confirmPassword
    );
    if (
      validationErrors.currentPassword ||
      validationErrors.newPassword ||
      validationErrors.confirmPassword
    ) {
      setFieldErrors(validationErrors);
      return;
    }

    setFieldErrors({});
    setSubmitting(true);

    try {
      await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword,
      });
      router.replace('/login');
    } catch (error) {
      if (error instanceof ApiError) {
        const nextErrors: ChangePasswordFormErrors = {};
        if (error.fieldErrors.current_password?.length) {
          nextErrors.currentPassword = error.fieldErrors.current_password[0];
        }
        if (error.fieldErrors.new_password?.length) {
          nextErrors.newPassword = error.fieldErrors.new_password[0];
        }
        if (error.fieldErrors.confirm_password?.length) {
          nextErrors.confirmPassword = error.fieldErrors.confirm_password[0];
        }
        setFieldErrors(nextErrors);
        setSubmitError(error.message);
      } else if (error instanceof Error) {
        setSubmitError(error.message);
      } else {
        setSubmitError('Unable to update password. Please try again.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.headerBar}>
        <AnimatedBackButton fallbackHref="/(tabs)/account" />
        <Text style={styles.headerTitle}>Change Password</Text>
        <View style={styles.headerSpacer} />
      </View>

      <KeyboardAwareContainer contentContainerStyle={styles.scrollContent}>
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Update your password</Text>
          <Text style={styles.cardSubtitle}>
            Choose a strong password at least 10 characters long. After updating your
            password, you will be asked to sign in again.
          </Text>

          {submitError ? (
            <View style={styles.bannerWrap}>
              <ErrorBanner
                message={submitError}
                onDismiss={() => setSubmitError(null)}
              />
            </View>
          ) : null}

          <View style={styles.fieldGroup}>
            <Text style={styles.fieldLabel}>Current Password</Text>
            <PasswordInput
              value={currentPassword}
              onChangeText={(value) => {
                setCurrentPassword(value);
                if (fieldErrors.currentPassword) {
                  setFieldErrors((prev) => ({
                    ...prev,
                    currentPassword: undefined,
                  }));
                }
              }}
              placeholder="Enter current password"
              placeholderTextColor={colors.placeholder}
              editable={!submitting}
              hasError={Boolean(fieldErrors.currentPassword)}
            />
            {fieldErrors.currentPassword ? (
              <Text style={styles.fieldErrorText}>
                {fieldErrors.currentPassword}
              </Text>
            ) : null}
          </View>

          <View style={styles.fieldGroup}>
            <Text style={styles.fieldLabel}>New Password</Text>
            <PasswordInput
              value={newPassword}
              onChangeText={(value) => {
                setNewPassword(value);
                if (fieldErrors.newPassword) {
                  setFieldErrors((prev) => ({
                    ...prev,
                    newPassword: undefined,
                  }));
                }
              }}
              placeholder="At least 10 characters"
              placeholderTextColor={colors.placeholder}
              editable={!submitting}
              hasError={Boolean(fieldErrors.newPassword)}
            />
            {fieldErrors.newPassword ? (
              <Text style={styles.fieldErrorText}>{fieldErrors.newPassword}</Text>
            ) : null}
          </View>

          <View style={styles.fieldGroup}>
            <Text style={styles.fieldLabel}>Confirm New Password</Text>
            <PasswordInput
              value={confirmPassword}
              onChangeText={(value) => {
                setConfirmPassword(value);
                if (fieldErrors.confirmPassword) {
                  setFieldErrors((prev) => ({
                    ...prev,
                    confirmPassword: undefined,
                  }));
                }
              }}
              placeholder="Re-enter new password"
              placeholderTextColor={colors.placeholder}
              editable={!submitting}
              hasError={Boolean(fieldErrors.confirmPassword)}
              onSubmitEditing={() => void handleSubmit()}
            />
            {fieldErrors.confirmPassword ? (
              <Text style={styles.fieldErrorText}>
                {fieldErrors.confirmPassword}
              </Text>
            ) : null}
          </View>

          <Pressable
            accessibilityRole="button"
            disabled={submitting}
            onPress={() => void handleSubmit()}
            style={({ pressed }) => [
              styles.submitBtn,
              submitting && styles.submitBtnDisabled,
              pressed && !submitting && styles.submitBtnPressed,
            ]}
          >
            {submitting ? (
              <View style={styles.submittingRow}>
                <ActivityIndicator size="small" color={colors.onPrimary} />
                <Text style={styles.submitBtnText}>Updating Password…</Text>
              </View>
            ) : (
              <Text style={styles.submitBtnText}>Update Password</Text>
            )}
          </Pressable>
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
    headerBar: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 18,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
      backgroundColor: colors.background,
    },
    headerTitle: {
      fontSize: 17,
      fontWeight: '700',
      color: colors.text,
    },
    headerSpacer: {
      width: 40,
    },
    scrollContent: {
      paddingHorizontal: 18,
      paddingVertical: 20,
    },
    card: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      padding: 18,
      borderWidth: 1,
      borderColor: colors.border,
    },
    cardTitle: {
      fontSize: 18,
      fontWeight: '700',
      color: colors.text,
    },
    cardSubtitle: {
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
    fieldErrorText: {
      marginTop: 5,
      fontSize: 12.5,
      color: colors.danger,
      fontWeight: '500',
    },
    submitBtn: {
      marginTop: 6,
      backgroundColor: colors.primary,
      borderRadius: 12,
      paddingVertical: 14,
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: 48,
    },
    submitBtnDisabled: {
      opacity: 0.7,
    },
    submitBtnPressed: {
      opacity: 0.86,
    },
    submittingRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
    },
    submitBtnText: {
      color: colors.onPrimary,
      fontSize: 15,
      fontWeight: '700',
    },
  });
