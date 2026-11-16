import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { LEAD_STATUS_META } from '../constants/leads.ts';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../context/AppThemeContext.tsx';
import type { LeadStatus } from '../types/index.ts';

export interface LeadStatusBadgeProps {
  status: LeadStatus;
  compact?: boolean;
}

export function LeadStatusBadge({ status, compact = false }: LeadStatusBadgeProps) {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();
  const meta = LEAD_STATUS_META[status] ?? LEAD_STATUS_META.NEW;

  const toneColors = (() => {
    switch (meta.tone) {
      case 'primary':
        return { bg: colors.accentSoft, fg: colors.primary };
      case 'info':
        return { bg: colors.infoSoft, fg: colors.info };
      case 'success':
        return { bg: colors.successSoft, fg: colors.success };
      case 'warning':
        return { bg: colors.warningSoft, fg: colors.warning };
      case 'danger':
        return { bg: colors.dangerSoft, fg: colors.danger };
      default:
        return { bg: colors.neutralSoft, fg: colors.neutral };
    }
  })();

  return (
    <View
      style={[
        styles.badge,
        compact && styles.badgeCompact,
        { backgroundColor: toneColors.bg },
      ]}
    >
      <Text
        style={[
          styles.badgeText,
          compact && styles.badgeTextCompact,
          { color: toneColors.fg },
        ]}
        numberOfLines={1}
      >
        {compact ? meta.shortLabel : meta.label}
      </Text>
    </View>
  );
}

const createStyles = (_colors: AppColors) =>
  StyleSheet.create({
    badge: {
      paddingHorizontal: 10,
      paddingVertical: 5,
      borderRadius: 999,
      alignSelf: 'flex-start',
    },
    badgeCompact: {
      paddingHorizontal: 8,
      paddingVertical: 3,
    },
    badgeText: {
      fontSize: 12,
      fontWeight: '700',
      letterSpacing: 0.2,
    },
    badgeTextCompact: {
      fontSize: 11,
    },
  });
