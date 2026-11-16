import React, { type ReactNode } from 'react';
import { router } from 'expo-router';
import {
  Pressable,
  type StyleProp,
  StyleSheet,
  Text,
  type ViewStyle,
} from 'react-native';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles } from '../context/AppThemeContext.tsx';

export interface AnimatedBackButtonProps {
  style?: StyleProp<ViewStyle>;
  onPress?: () => void;
  fallbackHref?: string;
  children?: ReactNode;
}

export function AnimatedBackButton({
  style,
  onPress,
  fallbackHref = '/(tabs)/leads',
  children,
}: AnimatedBackButtonProps) {
  const styles = useAppStyles(createStyles);

  const handlePress = () => {
    if (onPress) {
      onPress();
      return;
    }
    if (router.canGoBack()) {
      router.back();
    } else {
      router.replace(fallbackHref as never);
    }
  };

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel="Go back"
      onPress={handlePress}
      style={({ pressed }) => [
        styles.button,
        style,
        pressed && styles.buttonPressed,
      ]}
    >
      {children ?? <Text style={styles.icon}>‹</Text>}
    </Pressable>
  );
}

const createStyles = (colors: AppColors) =>
  StyleSheet.create({
    button: {
      width: 40,
      height: 40,
      borderRadius: 20,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
    },
    buttonPressed: {
      transform: [{ scale: 0.92 }],
      opacity: 0.75,
    },
    icon: {
      fontSize: 28,
      lineHeight: 30,
      color: colors.text,
      fontWeight: '400',
      marginTop: -2,
    },
  });
