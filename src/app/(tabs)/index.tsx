import React, { useCallback, useState } from 'react';
import { router, useFocusEffect } from 'expo-router';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ErrorBanner } from '../../components/ErrorBanner.tsx';
import { LeadStatusBadge } from '../../components/LeadStatusBadge.tsx';
import { APP_NAME } from '../../constants/config.ts';
import type { AppColors } from '../../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../../context/AppThemeContext.tsx';
import { useAuth } from '../../context/AuthContext.tsx';
import { fetchMyCallHistory } from '../../services/calls.ts';
import { fetchMyFollowUps } from '../../services/followUps.ts';
import { fetchAssignedLeads } from '../../services/leads.ts';
import type { EmployeeLead } from '../../types/index.ts';
import { formatRelativeTime, getInitials } from '../../utils/format.ts';

function getTimeOfDayGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

interface DashboardStats {
  totalAssigned: number;
  newCount: number;
  contactedCount: number;
  interestedCount: number;
  overdueFollowUps: number;
  todayFollowUps: number;
  totalCallsLogged: number;
  recentLeads: EmployeeLead[];
}

export default function EmployeeHomeScreen() {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();
  const { user, refreshProfile, signOut } = useAuth();

  const [stats, setStats] = useState<DashboardStats>({
    totalAssigned: 0,
    newCount: 0,
    contactedCount: 0,
    interestedCount: 0,
    overdueFollowUps: 0,
    todayFollowUps: 0,
    totalCallsLogged: 0,
    recentLeads: [],
  });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDashboard = useCallback(async (isPullRefresh = false) => {
    if (isPullRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);

    try {
      const [
        allLeadsPage,
        newPage,
        contactedPage,
        interestedPage,
        overdueFuPage,
        todayFuPage,
        callsPage,
      ] = await Promise.all([
        fetchAssignedLeads({ page: 1, page_size: 5 }),
        fetchAssignedLeads({ page: 1, page_size: 1, status: 'NEW' }),
        fetchAssignedLeads({ page: 1, page_size: 1, status: 'CONTACTED' }),
        fetchAssignedLeads({ page: 1, page_size: 1, status: 'INTERESTED' }),
        fetchMyFollowUps(
          { due_state: 'overdue', page: 1, page_size: 1 },
          { enrichLeadPhones: false }
        ).catch(() => ({ count: 0, next: null, previous: null, results: [] })),
        fetchMyFollowUps(
          { due_state: 'today', page: 1, page_size: 1 },
          { enrichLeadPhones: false }
        ).catch(() => ({ count: 0, next: null, previous: null, results: [] })),
        fetchMyCallHistory({ page: 1, page_size: 1 }).catch(() => ({
          count: 0,
          next: null,
          previous: null,
          results: [],
        })),
      ]);

      setStats({
        totalAssigned: allLeadsPage.count,
        newCount: newPage.count,
        contactedCount: contactedPage.count,
        interestedCount: interestedPage.count,
        overdueFollowUps: overdueFuPage.count,
        todayFollowUps: todayFuPage.count,
        totalCallsLogged: callsPage.count,
        recentLeads: allLeadsPage.results,
      });
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : 'Unable to load dashboard summary. Pull down to retry.';
      setError(message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void loadDashboard(false);
    }, [loadDashboard])
  );

  const handlePullRefresh = async () => {
    await Promise.all([refreshProfile(), loadDashboard(true)]);
  };

  const confirmLogout = () => {
    Alert.alert('Sign Out', 'Are you sure you want to sign out of IQRA TCS?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign Out',
        style: 'destructive',
        onPress: () => {
          void signOut().then(() => {
            router.replace('/login');
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
            onRefresh={() => void handlePullRefresh()}
            tintColor={colors.primary}
          />
        }
      >
        <View style={styles.topBar}>
          <View>
            <Text style={styles.appBrandText}>{APP_NAME}</Text>
            <Text style={styles.greetingText}>{getTimeOfDayGreeting()}</Text>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Sign Out"
            onPress={confirmLogout}
            style={({ pressed }) => [
              styles.logoutPill,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={styles.logoutPillText}>Sign Out</Text>
          </Pressable>
        </View>

        {/* Employee Identity Card */}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="View employee account details"
          onPress={() => router.push('/(tabs)/account')}
          style={({ pressed }) => [
            styles.identityCard,
            pressed && styles.buttonPressed,
          ]}
        >
          <View style={styles.identityHeader}>
            <View style={styles.avatarCircle}>
              <Text style={styles.avatarText}>{initials}</Text>
            </View>
            <View style={styles.identityTextCol}>
              <View style={styles.nameBadgeRow}>
                <Text style={styles.employeeName} numberOfLines={1}>
                  {displayName}
                </Text>
                <View style={styles.roleBadge}>
                  <Text style={styles.roleBadgeText}>{user?.role || 'EMPLOYEE'}</Text>
                </View>
              </View>
              <Text style={styles.employeeSubtext} numberOfLines={1}>
                @{user?.username}
                {user?.designation ? ` · ${user.designation}` : ''}
              </Text>
              {user?.email || user?.phone ? (
                <Text style={styles.employeeContactText} numberOfLines={1}>
                  {[user?.phone, user?.email].filter(Boolean).join(' · ')}
                </Text>
              ) : null}
            </View>
          </View>
        </Pressable>

        {error ? (
          <View style={styles.bannerWrap}>
            <ErrorBanner
              message={error}
              actionLabel="Retry"
              onAction={() => void loadDashboard(false)}
            />
          </View>
        ) : null}

        {/* Calling & Follow-Up Alerts */}
        <View style={styles.sectionHeaderRow}>
          <Text style={styles.sectionTitle}>Calling & Follow-Up Queue</Text>
          {loading && !refreshing ? (
            <ActivityIndicator size="small" color={colors.primary} />
          ) : null}
        </View>

        <View style={styles.statsRowThree}>
          <Pressable
            accessibilityRole="button"
            onPress={() =>
              router.push({
                pathname: '/(tabs)/follow-ups',
                params: { bucket: 'OVERDUE' },
              })
            }
            style={({ pressed }) => [
              styles.statCardCompact,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={[styles.statValue, { color: colors.danger }]}>
              {stats.overdueFollowUps}
            </Text>
            <Text style={styles.statLabel}>Overdue</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            onPress={() =>
              router.push({
                pathname: '/(tabs)/follow-ups',
                params: { bucket: 'TODAY' },
              })
            }
            style={({ pressed }) => [
              styles.statCardCompact,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={[styles.statValue, { color: colors.warning }]}>
              {stats.todayFollowUps}
            </Text>
            <Text style={styles.statLabel}>Due Today</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            onPress={() => router.push('/(tabs)/history')}
            style={({ pressed }) => [
              styles.statCardCompact,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={[styles.statValue, { color: colors.primary }]}>
              {stats.totalCallsLogged}
            </Text>
            <Text style={styles.statLabel}>My Calls</Text>
          </Pressable>
        </View>

        {/* Assigned Leads Overview */}
        <View style={styles.sectionHeaderRow}>
          <Text style={styles.sectionTitle}>Assigned Leads Summary</Text>
        </View>

        <View style={styles.statsGrid}>
          <Pressable
            accessibilityRole="button"
            onPress={() =>
              router.push({ pathname: '/(tabs)/leads', params: { status: 'ALL' } })
            }
            style={({ pressed }) => [
              styles.statCard,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={styles.statValue}>{stats.totalAssigned}</Text>
            <Text style={styles.statLabel}>Total Assigned</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            onPress={() =>
              router.push({ pathname: '/(tabs)/leads', params: { status: 'NEW' } })
            }
            style={({ pressed }) => [
              styles.statCard,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={[styles.statValue, { color: colors.primary }]}>
              {stats.newCount}
            </Text>
            <Text style={styles.statLabel}>New Leads</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            onPress={() =>
              router.push({ pathname: '/(tabs)/leads', params: { status: 'CONTACTED' } })
            }
            style={({ pressed }) => [
              styles.statCard,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={[styles.statValue, { color: colors.info }]}>
              {stats.contactedCount}
            </Text>
            <Text style={styles.statLabel}>Contacted</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            onPress={() =>
              router.push({ pathname: '/(tabs)/leads', params: { status: 'INTERESTED' } })
            }
            style={({ pressed }) => [
              styles.statCard,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={[styles.statValue, { color: colors.success }]}>
              {stats.interestedCount}
            </Text>
            <Text style={styles.statLabel}>Interested</Text>
          </Pressable>
        </View>

        {/* Quick Actions */}
        <View style={styles.quickActionRow}>
          <Pressable
            accessibilityRole="button"
            onPress={() => router.push('/(tabs)/leads')}
            style={({ pressed }) => [
              styles.primaryActionBtn,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={styles.primaryActionBtnText}>Assigned Leads</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            onPress={() => router.push('/(tabs)/follow-ups')}
            style={({ pressed }) => [
              styles.secondaryActionBtn,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={styles.secondaryActionBtnText}>My Follow-Ups</Text>
          </Pressable>
        </View>

        {/* Recent Assigned Leads Preview */}
        <View style={styles.sectionHeaderRow}>
          <Text style={styles.sectionTitle}>Recently Updated Leads</Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => router.push('/(tabs)/leads')}
          >
            <Text style={styles.viewAllLink}>View All ›</Text>
          </Pressable>
        </View>

        {loading && stats.recentLeads.length === 0 ? (
          <View style={styles.emptyCard}>
            <ActivityIndicator size="small" color={colors.primary} />
            <Text style={styles.emptyCardSubtitle}>Loading assigned leads…</Text>
          </View>
        ) : stats.recentLeads.length === 0 ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyCardTitle}>No assigned leads yet</Text>
            <Text style={styles.emptyCardSubtitle}>
              Leads assigned to your account by an administrator will appear here.
            </Text>
          </View>
        ) : (
          <View style={styles.recentList}>
            {stats.recentLeads.map((lead) => (
              <Pressable
                key={lead.id}
                accessibilityRole="button"
                accessibilityLabel={`Open lead ${lead.name}`}
                onPress={() =>
                  router.push({
                    pathname: '/lead-details',
                    params: { id: String(lead.id) },
                  })
                }
                style={({ pressed }) => [
                  styles.leadPreviewCard,
                  pressed && styles.buttonPressed,
                ]}
              >
                <View style={styles.leadPreviewHeader}>
                  <Text style={styles.leadPreviewName} numberOfLines={1}>
                    {lead.name}
                  </Text>
                  <LeadStatusBadge status={lead.status} compact />
                </View>
                <Text style={styles.leadPreviewPhone}>{lead.phone}</Text>
                <View style={styles.leadPreviewFooter}>
                  <Text style={styles.leadPreviewCourse} numberOfLines={1}>
                    {lead.course_interest || 'General Enquiry'}
                  </Text>
                  <Text style={styles.leadPreviewTime}>
                    {formatRelativeTime(lead.updated_at || lead.created_at)}
                  </Text>
                </View>
              </Pressable>
            ))}
          </View>
        )}
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
      paddingBottom: 32,
    },
    topBar: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 16,
    },
    appBrandText: {
      fontSize: 12,
      fontWeight: '800',
      letterSpacing: 1,
      color: colors.primary,
      textTransform: 'uppercase',
    },
    greetingText: {
      fontSize: 22,
      fontWeight: '800',
      color: colors.text,
      marginTop: 2,
    },
    logoutPill: {
      paddingHorizontal: 13,
      paddingVertical: 8,
      borderRadius: 999,
      backgroundColor: colors.dangerSoft,
    },
    logoutPillText: {
      fontSize: 13,
      fontWeight: '700',
      color: colors.danger,
    },
    identityCard: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      padding: 16,
      borderWidth: 1,
      borderColor: colors.border,
      marginBottom: 18,
    },
    identityHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 14,
    },
    avatarCircle: {
      width: 52,
      height: 52,
      borderRadius: 26,
      backgroundColor: colors.primaryDark,
      alignItems: 'center',
      justifyContent: 'center',
    },
    avatarText: {
      color: '#FFFFFF',
      fontSize: 18,
      fontWeight: '800',
    },
    identityTextCol: {
      flex: 1,
    },
    nameBadgeRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      flexWrap: 'wrap',
    },
    employeeName: {
      fontSize: 17,
      fontWeight: '700',
      color: colors.text,
      flexShrink: 1,
    },
    roleBadge: {
      paddingHorizontal: 8,
      paddingVertical: 2,
      borderRadius: 999,
      backgroundColor: colors.accentSoft,
    },
    roleBadgeText: {
      fontSize: 10.5,
      fontWeight: '800',
      color: colors.primary,
    },
    employeeSubtext: {
      fontSize: 13,
      color: colors.secondary,
      marginTop: 3,
    },
    employeeContactText: {
      fontSize: 12,
      color: colors.muted,
      marginTop: 3,
    },
    bannerWrap: {
      marginBottom: 16,
    },
    sectionHeaderRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 10,
      marginTop: 4,
    },
    sectionTitle: {
      fontSize: 16,
      fontWeight: '700',
      color: colors.text,
    },
    viewAllLink: {
      fontSize: 13.5,
      fontWeight: '700',
      color: colors.primary,
    },
    statsRowThree: {
      flexDirection: 'row',
      gap: 10,
      marginBottom: 16,
    },
    statCardCompact: {
      flex: 1,
      backgroundColor: colors.surface,
      borderRadius: 14,
      paddingVertical: 12,
      paddingHorizontal: 10,
      borderWidth: 1,
      borderColor: colors.border,
      alignItems: 'center',
    },
    statsGrid: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 10,
      marginBottom: 16,
    },
    statCard: {
      flexBasis: '48%',
      flexGrow: 1,
      backgroundColor: colors.surface,
      borderRadius: 14,
      paddingVertical: 14,
      paddingHorizontal: 14,
      borderWidth: 1,
      borderColor: colors.border,
    },
    statValue: {
      fontSize: 22,
      fontWeight: '800',
      color: colors.text,
    },
    statLabel: {
      fontSize: 12,
      fontWeight: '600',
      color: colors.muted,
      marginTop: 4,
    },
    quickActionRow: {
      flexDirection: 'row',
      gap: 10,
      marginBottom: 20,
    },
    primaryActionBtn: {
      flex: 1,
      backgroundColor: colors.primary,
      borderRadius: 12,
      paddingVertical: 13,
      alignItems: 'center',
      justifyContent: 'center',
    },
    primaryActionBtnText: {
      color: colors.onPrimary,
      fontSize: 14,
      fontWeight: '700',
    },
    secondaryActionBtn: {
      flex: 1,
      backgroundColor: colors.surface,
      borderRadius: 12,
      paddingVertical: 13,
      alignItems: 'center',
      justifyContent: 'center',
      borderWidth: 1,
      borderColor: colors.border,
    },
    secondaryActionBtnText: {
      color: colors.text,
      fontSize: 13.5,
      fontWeight: '600',
    },
    emptyCard: {
      backgroundColor: colors.surface,
      borderRadius: 14,
      padding: 22,
      borderWidth: 1,
      borderColor: colors.border,
      alignItems: 'center',
    },
    emptyCardTitle: {
      fontSize: 15,
      fontWeight: '700',
      color: colors.text,
    },
    emptyCardSubtitle: {
      fontSize: 13,
      color: colors.muted,
      textAlign: 'center',
      marginTop: 6,
      lineHeight: 18,
    },
    recentList: {
      gap: 10,
    },
    leadPreviewCard: {
      backgroundColor: colors.surface,
      borderRadius: 14,
      padding: 14,
      borderWidth: 1,
      borderColor: colors.border,
    },
    leadPreviewHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
    },
    leadPreviewName: {
      flex: 1,
      fontSize: 15.5,
      fontWeight: '700',
      color: colors.text,
    },
    leadPreviewPhone: {
      fontSize: 13.5,
      fontWeight: '600',
      color: colors.secondary,
      marginTop: 4,
    },
    leadPreviewFooter: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginTop: 8,
      gap: 8,
    },
    leadPreviewCourse: {
      flex: 1,
      fontSize: 12.5,
      color: colors.muted,
    },
    leadPreviewTime: {
      fontSize: 12,
      color: colors.muted,
    },
    buttonPressed: {
      opacity: 0.8,
    },
  });
