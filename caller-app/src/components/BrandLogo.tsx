import React from 'react';
import {
  Image,
  StyleSheet,
  View,
  type ImageSourcePropType,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { useAppTheme } from '../context/AppThemeContext.tsx';

const BRAND_LOGO_SOURCE: ImageSourcePropType = require('../../assets/images/brand-logo.png');

export interface BrandLogoProps {
  size?: number;
  accessibilityLabel?: string;
  style?: StyleProp<ViewStyle>;
}

/**
 * Official IQRA TCS crest badge.
 * Uses a clean white circular backing surface so the royal-blue scalloped seal
 * preserves full contrast in both light and dark themes without altering the logo.
 */
export function BrandLogo({
  size = 72,
  accessibilityLabel = 'IQRA TCS logo',
  style,
}: BrandLogoProps) {
  const { colors } = useAppTheme();
  const padding = Math.max(3, Math.round(size * 0.06));
  const innerSize = size - padding * 2;

  return (
    <View
      style={[
        styles.container,
        {
          width: size,
          height: size,
          borderRadius: size / 2,
          padding,
          borderColor: colors.border,
        },
        style,
      ]}
    >
      <Image
        source={BRAND_LOGO_SOURCE}
        accessibilityRole="image"
        accessibilityLabel={accessibilityLabel}
        resizeMode="contain"
        style={{ width: innerSize, height: innerSize }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#0F172A',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 6,
    elevation: 2,
  },
});
