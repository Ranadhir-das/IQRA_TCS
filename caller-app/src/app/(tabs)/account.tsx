import React, { useState } from 'react';
import { router } from 'expo-router';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { BrandLogo } from '../../components/BrandLogo.tsx';
import { API_BASE_URL, APP_ENV, APP_NAME } from '../../constants/config.ts';
import type { AppColors } from '../../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../../context/AppThemeContext.tsx';
import { useAuth } from '../../context/AuthContext.tsx';
import { formatDateTime, formatShortDate, getInitials } from '../../utils/format.ts';

function ProfileDetailRow({
  label,
  value,
  colors,
}: {
  label: string;
  value: string;
  colors: AppColors;
}) {
  return (
    <View style={[detailStyles.row, { borderBottomColor: colors.border }]}>
      <Text style={[detailStyles.label, { color: colors.muted }]}>{label}</Text>
      <Text style={[detailStyles.value, { color: colors.text }]} selectable>
        {value || '—'}
      </Text>
    </View>
  );
}

const detailStyles = StyleSheet.create({
  row: {
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 14,
  },
  label: {
    fontSize: 13.5,
    fontWeight: '600',
  },
  value: {
    flex: 1,
    fontSize: 14,
    fontWeight: '600',
    textAlign: 'right',
  },
});

export default function EmployeeAccountScreen() {
  const styles = useAppStyles(createStyles);
  const { colors, mode, toggleTheme } = useAppTheme();
  const { user, refreshProfile, signOut } = useAuth();

  const [refreshing, setRefreshing] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refreshProfile();
    } finally {
      setRefreshing(false);
    }
  };

  const handleSignOut = () => {
    Alert.alert('Sign Out', 'Are you sure you want to sign out of your employee account?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign Out',
        style: 'destructive',
        onPress: () => {
          setSigningOut(true);
          void signOut()
            .then(() => {
              router.replace('/login');
            })
            .finally(() => {
              setSigningOut(false);
            });
        },
      },
    ]);
  };

  const displayName = user?.full_name?.trim() || user?.username || 'Employee';
  const initials = getInitials(user?.full_name, user?.username);

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => void handleRefresh()}
            tintColor={colors.primary}
          />
        }
      >
        <Text style={styles.screenTitle}>Employee Account</Text>

        {/* Identity Banner */}
        <View style={styles.profileCard}>
          <View style={styles.avatarLarge}>
            <Text style={styles.avatarLargeText}>{initials}</Text>
          </View>
          <Text style={styles.profileName}>{displayName}</Text>
          <Text style={styles.profileUsername}>@{user?.username}</Text>
          <View style={styles.badgesRow}>
            <View style={styles.rolePill}>
              <Text style={styles.rolePillText}>{user?.role || 'EMPLOYEE'}</Text>
            </View>
            <View style={styles.activePill}>
              <Text style={styles.activePillText}>
                {user?.is_active ? 'ACTIVE' : 'INACTIVE'}
              </Text>
            </View>
          </View>
        </View>

        {/* Employee Information */}
        <View style={styles.sectionCard}>
          <Text style={styles.sectionCardTitle}>Profile Information</Text>
          <ProfileDetailRow
            label="Full Name"
            value={user?.full_name || '—'}
            colors={colors}
          />
          <ProfileDetailRow
            label="Username"
            value={user?.username || '—'}
            colors={colors}
          />
          <ProfileDetailRow
            label="Designation"
            value={user?.designation || 'Not specified'}
            colors={colors}
          />
          <ProfileDetailRow
            label="Phone"
            value={user?.phone || 'Not provided'}
            colors={colors}
          />
          <ProfileDetailRow
            label="Email"
            value={user?.email || 'Not provided'}
            colors={colors}
          />
          <ProfileDetailRow
            label="Joined"
            value={formatShortDate(user?.date_joined)}
            colors={colors}
          />
          <ProfileDetailRow
            label="Last Sign-In"
            value={formatDateTime(user?.last_login)}
            colors={colors}
          />
        </View>

        {/* Preferences & Security */}
        <View style={styles.sectionCard}>
          <Text style={styles.sectionCardTitle}>Security & Preferences</Text>

          <View style={styles.preferenceRow}>
            <View>
              <Text style={styles.preferenceTitle}>Dark Mode</Text>
              <Text style={styles.preferenceSubtext}>
                Current theme: {mode === 'dark' ? 'Dark' : 'Light'}
              </Text>
            </View>
            <Switch
              value={mode === 'dark'}
              onValueChange={toggleTheme}
              trackColor={{ false: colors.border, true: colors.primary }}
            />
          </View>

          <Pressable
            accessibilityRole="button"
            onPress={() => router.push('/change-password')}
            style={({ pressed }) => [
              styles.actionRowButton,
              pressed && styles.buttonPressed,
            ]}
          >
            <View>
              <Text style={styles.preferenceTitle}>Change Password</Text>
              <Text style={styles.preferenceSubtext}>
                Update your IQRA TCS employee password
              </Text>
            </View>
            <Text style={styles.chevronText}>›</Text>
          </Pressable>
        </View>

        {/* Application Metadata */}
        <View style={styles.sectionCard}>
          <View style={styles.appInfoBrandHeader}>
            <BrandLogo size={36} accessibilityLabel="IQRA TCS logo" />
            <Text style={styles.sectionCardTitleNoMargin}>Application Info</Text>
          </View>
          <ProfileDetailRow label="App" value={APP_NAME} colors={colors} />
          <ProfileDetailRow
            label="Environment"
            value={APP_ENV.toUpperCase()}
            colors={colors}
          />
          <ProfileDetailRow label="API Server" value={API_BASE_URL} colors={colors} />
        </View>

        {/* Sign Out */}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Sign Out"
          disabled={signingOut}
          onPress={handleSignOut}
          style={({ pressed }) => [
            styles.signOutButton,
            pressed && styles.buttonPressed,
          ]}
        >
          {signingOut ? (
            <ActivityIndicator size="small" color={colors.danger} />
          ) : (
            <Text style={styles.signOutButtonText}>Sign Out</Text>
          )}
        </Pressable>
      </ScrollView>
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
      paddingHorizontal: 18,
      paddingTop: 14,
      paddingBottom: 36,
    },
    screenTitle: {
      fontSize: 22,
      fontWeight: '800',
      color: colors.text,
      marginBottom: 14,
    },
    profileCard: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      padding: 20,
      borderWidth: 1,
      borderColor: colors.border,
      alignItems: 'center',
      marginBottom: 16,
    },
    avatarLarge: {
      width: 68,
      height: 68,
      borderRadius: 34,
      backgroundColor: colors.primaryDark,
      alignItems: 'center',
      justifyContent: 'center',
      marginBottom: 10,
    },
    avatarLargeText: {
      color: '#FFFFFF',
      fontSize: 24,
      fontWeight: '800',
    },
    profileName: {
      fontSize: 19,
      fontWeight: '800',
      color: colors.text,
      textAlign: 'center',
    },
    profileUsername: {
      fontSize: 13.5,
      color: colors.muted,
      marginTop: 2,
    },
    badgesRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      marginTop: 10,
    },
    rolePill: {
      paddingHorizontal: 10,
      paddingVertical: 3,
      borderRadius: 999,
      backgroundColor: colors.accentSoft,
    },
    rolePillText: {
      fontSize: 11,
      fontWeight: '800',
      color: colors.primary,
    },
    activePill: {
      paddingHorizontal: 10,
      paddingVertical: 3,
      borderRadius: 999,
      backgroundColor: colors.successSoft,
    },
    activePillText: {
      fontSize: 11,
      fontWeight: '800',
      color: colors.success,
    },
    sectionCard: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      paddingHorizontal: 16,
      paddingTop: 16,
      paddingBottom: 8,
      borderWidth: 1,
      borderColor: colors.border,
      marginBottom: 16,
    },
    sectionCardTitle: {
      fontSize: 15,
      fontWeight: '700',
      color: colors.text,
      marginBottom: 6,
    },
    appInfoBrandHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      marginBottom: 6,
    },
    sectionCardTitleNoMargin: {
      fontSize: 15,
      fontWeight: '700',
      color: colors.text,
    },
    preferenceRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingVertical: 12,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: colors.border,
    },
    actionRowButton: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingVertical: 14,
    },
    preferenceTitle: {
      fontSize: 14.5,
      fontWeight: '600',
      color: colors.text,
    },
    preferenceSubtext: {
      fontSize: 12.5,
      color: colors.muted,
      marginTop: 2,
    },
    chevronText: {
      fontSize: 22,
      color: colors.muted,
      fontWeight: '500',
    },
    signOutButton: {
      backgroundColor: colors.dangerSoft,
      borderWidth: 1,
      borderColor: colors.danger,
      borderRadius: 14,
      paddingVertical: 14,
      alignItems: 'center',
      justifyContent: 'center',
      marginTop: 4,
    },
    signOutButtonText: {
      fontSize: 15,
      fontWeight: '700',
      color: colors.danger,
    },
    buttonPressed: {
      opacity: 0.78,
    },
  });
