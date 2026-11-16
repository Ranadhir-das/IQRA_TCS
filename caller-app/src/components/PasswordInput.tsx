import React, { useState } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type TextInputProps,
} from 'react-native';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles } from '../context/AppThemeContext.tsx';

export interface PasswordInputProps extends TextInputProps {
  hasError?: boolean;
}

export function PasswordInput({
  style,
  editable = true,
  hasError = false,
  ...props
}: PasswordInputProps) {
  const [visible, setVisible] = useState(false);
  const styles = useAppStyles(createStyles);

  return (
    <View style={styles.container}>
      <TextInput
        {...props}
        editable={editable}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry={!visible}
        style={[
          styles.input,
          hasError && styles.inputError,
          !editable && styles.inputDisabled,
          style,
        ]}
      />
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={visible ? 'Hide password' : 'Show password'}
        accessibilityState={{ disabled: !editable }}
        disabled={!editable}
        onPress={() => setVisible((prev) => !prev)}
        style={({ pressed }) => [
          styles.toggleButton,
          (!editable || pressed) && styles.toggleButtonPressed,
        ]}
      >
        <Text style={styles.toggleText}>{visible ? 'Hide' : 'Show'}</Text>
      </Pressable>
    </View>
  );
}

const createStyles = (colors: AppColors) =>
  StyleSheet.create({
    container: {
      position: 'relative',
      justifyContent: 'center',
    },
    input: {
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      paddingHorizontal: 14,
      paddingVertical: 13,
      paddingRight: 68,
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
    toggleButton: {
      position: 'absolute',
      right: 6,
      top: 4,
      bottom: 4,
      paddingHorizontal: 10,
      minHeight: 40,
      alignItems: 'center',
      justifyContent: 'center',
      borderRadius: 8,
    },
    toggleButtonPressed: {
      opacity: 0.65,
    },
    toggleText: {
      fontSize: 13,
      fontWeight: '600',
      color: colors.primary,
    },
  });
