import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../context/AppThemeContext.tsx';

export interface ErrorBannerProps {
  message: string | null | undefined;
  variant?: 'error' | 'info' | 'success';
  onDismiss?: () => void;
  actionLabel?: string;
  onAction?: () => void;
}

export function ErrorBanner({
  message,
  variant = 'error',
  onDismiss,
  actionLabel,
  onAction,
}: ErrorBannerProps) {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();

  if (!message) return null;

  const palette =
    variant === 'info'
      ? { bg: colors.infoSoft, border: colors.info, text: colors.info }
      : variant === 'success'
        ? { bg: colors.successSoft, border: colors.success, text: colors.success }
        : { bg: colors.dangerSoft, border: colors.danger, text: colors.danger };

  return (
    <View
      accessibilityRole="alert"
      style={[
        styles.banner,
        { backgroundColor: palette.bg, borderColor: palette.border },
      ]}
    >
      <Text style={[styles.message, { color: palette.text }]}>{message}</Text>
      <View style={styles.actions}>
        {actionLabel && onAction ? (
          <Pressable
            accessibilityRole="button"
            onPress={onAction}
            style={styles.actionButton}
          >
            <Text style={[styles.actionText, { color: palette.text }]}>
              {actionLabel}
            </Text>
          </Pressable>
        ) : null}
        {onDismiss ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Dismiss message"
            onPress={onDismiss}
            style={styles.dismissButton}
          >
            <Text style={[styles.dismissText, { color: palette.text }]}>✕</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const createStyles = (_colors: AppColors) =>
  StyleSheet.create({
    banner: {
      borderWidth: 1,
      borderRadius: 12,
      paddingHorizontal: 14,
      paddingVertical: 12,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 10,
    },
    message: {
      flex: 1,
      fontSize: 13.5,
      lineHeight: 19,
      fontWeight: '500',
    },
    actions: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
    },
    actionButton: {
      paddingHorizontal: 8,
      paddingVertical: 4,
    },
    actionText: {
      fontSize: 13,
      fontWeight: '700',
      textDecorationLine: 'underline',
    },
    dismissButton: {
      paddingHorizontal: 6,
      paddingVertical: 2,
    },
    dismissText: {
      fontSize: 14,
      fontWeight: '700',
    },
  });
