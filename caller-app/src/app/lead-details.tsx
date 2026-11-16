import React, { useCallback, useState } from 'react';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
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
import { AnimatedBackButton } from '../components/AnimatedBackButton.tsx';
import { ErrorBanner } from '../components/ErrorBanner.tsx';
import { LeadStatusBadge } from '../components/LeadStatusBadge.tsx';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../context/AppThemeContext.tsx';
import { ApiError } from '../services/api.ts';
import { fetchMyCallHistory } from '../services/calls.ts';
import { openNativeDialer } from '../services/dialer.ts';
import { completeFollowUp, fetchMyFollowUps } from '../services/followUps.ts';
import { fetchAssignedLeadDetail } from '../services/leads.ts';
import type { CallHistoryItem, EmployeeFollowUp, EmployeeLead } from '../types/index.ts';
import { formatDateTime, formatDurationSeconds } from '../utils/format.ts';

function DetailField({
  label,
  value,
  colors,
}: {
  label: string;
  value: string;
  colors: AppColors;
}) {
  return (
    <View style={[fieldStyles.row, { borderBottomColor: colors.border }]}>
      <Text style={[fieldStyles.label, { color: colors.muted }]}>{label}</Text>
      <Text style={[fieldStyles.value, { color: colors.text }]} selectable>
        {value || 'Not provided'}
      </Text>
    </View>
  );
}

const fieldStyles = StyleSheet.create({
  row: {
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  label: {
    fontSize: 12.5,
    fontWeight: '600',
    marginBottom: 4,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  value: {
    fontSize: 15,
    fontWeight: '600',
  },
});

export default function LeadDetailsScreen() {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();
  const { id } = useLocalSearchParams<{ id?: string }>();

  const [lead, setLead] = useState<EmployeeLead | null>(null);
  const [pendingFollowUps, setPendingFollowUps] = useState<EmployeeFollowUp[]>([]);
  const [recentCalls, setRecentCalls] = useState<CallHistoryItem[]>([]);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const [dialerBanner, setDialerBanner] = useState<{
    phone: string;
    openedAtIso: string;
  } | null>(null);
  const [dialerError, setDialerError] = useState<string | null>(null);
  const [completingFollowUpId, setCompletingFollowUpId] = useState<number | null>(null);

  const loadLeadAndActivity = useCallback(
    async (isPullRefresh = false) => {
      if (!id) {
        setError('Lead ID is missing.');
        setLoading(false);
        return;
      }

      if (isPullRefresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      setError(null);
      setNotFound(false);

      try {
        const numericId = Number(id);
        const [leadData, followUpsPage, callsPage] = await Promise.all([
          fetchAssignedLeadDetail(numericId),
          fetchMyFollowUps(
            { lead_id: numericId, status: 'PENDING', page: 1, page_size: 10 },
            { enrichLeadPhones: false }
          ).catch(() => ({ count: 0, next: null, previous: null, results: [] })),
          fetchMyCallHistory({ lead_id: numericId, page: 1, page_size: 5 }).catch(
            () => ({ count: 0, next: null, previous: null, results: [] })
          ),
        ]);

        setLead(leadData);
        setPendingFollowUps(followUpsPage.results);
        setRecentCalls(callsPage.results);
      } catch (err) {
        if (err instanceof ApiError && (err.status === 404 || err.status === 403)) {
          setNotFound(true);
          setError(
            'This lead is no longer assigned to your account or has been archived.'
          );
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError('Could not load lead details. Please try again.');
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [id]
  );

  useFocusEffect(
    useCallback(() => {
      void loadLeadAndActivity(false);
    }, [loadLeadAndActivity])
  );

  const handleClickToCall = async (rawPhone: string) => {
    setDialerError(null);
    try {
      const result = await openNativeDialer(rawPhone);
      setDialerBanner({
        phone: result.normalizedPhone,
        openedAtIso: result.openedAtIso,
      });
    } catch (err) {
      const msg =
        err instanceof Error
          ? err.message
          : 'Unable to open phone dialer for this number.';
      setDialerError(msg);
      Alert.alert('Cannot Place Call', msg);
    }
  };

  const handleCompleteFollowUp = (followUp: EmployeeFollowUp) => {
    if (completingFollowUpId !== null) return;
    Alert.alert(
      'Complete Follow-Up',
      'Mark this pending follow-up as completed?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Mark Completed',
          onPress: () => {
            setCompletingFollowUpId(followUp.id);
            void completeFollowUp(followUp.id)
              .then(() => loadLeadAndActivity(true))
              .catch((err) => {
                Alert.alert(
                  'Error',
                  err instanceof Error
                    ? err.message
                    : 'Could not mark follow-up completed.'
                );
              })
              .finally(() => setCompletingFollowUpId(null));
          },
        },
      ]
    );
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      {/* Top Navigation Bar */}
      <View style={styles.headerBar}>
        <AnimatedBackButton fallbackHref="/(tabs)/leads" />
        <View style={styles.headerTitleCol}>
          <Text style={styles.headerTitle} numberOfLines={1}>
            Lead Details
          </Text>
          {id ? <Text style={styles.headerSubtitle}>Lead #{id}</Text> : null}
        </View>
        <View style={styles.headerSpacer} />
      </View>

      {loading && !lead ? (
        <View style={styles.centerState}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.centerStateSubtitle}>Loading lead details…</Text>
        </View>
      ) : notFound || (!lead && error) ? (
        <View style={styles.centerState}>
          <Text style={styles.centerStateTitle}>
            {notFound ? 'Lead Unavailable' : 'Could Not Load Lead'}
          </Text>
          <Text style={styles.centerStateSubtitle}>
            {error || 'This lead could not be loaded.'}
          </Text>
          <View style={styles.errorActionsRow}>
            {!notFound ? (
              <Pressable
                accessibilityRole="button"
                onPress={() => void loadLeadAndActivity(false)}
                style={styles.primaryBtn}
              >
                <Text style={styles.primaryBtnText}>Retry</Text>
              </Pressable>
            ) : null}
            <Pressable
              accessibilityRole="button"
              onPress={() => router.replace('/(tabs)/leads')}
              style={styles.secondaryBtn}
            >
              <Text style={styles.secondaryBtnText}>Back to Assigned Leads</Text>
            </Pressable>
          </View>
        </View>
      ) : lead ? (
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => void loadLeadAndActivity(true)}
              tintColor={colors.primary}
            />
          }
        >
          {error ? (
            <ErrorBanner
              message={error}
              actionLabel="Retry"
              onAction={() => void loadLeadAndActivity(true)}
            />
          ) : null}

          {dialerError ? (
            <ErrorBanner
              message={dialerError}
              onDismiss={() => setDialerError(null)}
            />
          ) : null}

          {dialerBanner ? (
            <ErrorBanner
              variant="info"
              message={`Dialer opened for ${dialerBanner.phone}. Opening the dialer does not automatically log a call — tap "Log Call Outcome" once your call finishes.`}
              actionLabel="Log Outcome"
              onAction={() =>
                router.push({
                  pathname: '/log-call',
                  params: {
                    leadId: String(lead.id),
                    leadName: lead.name,
                    leadPhone: dialerBanner.phone,
                    dialerOpenedAt: dialerBanner.openedAtIso,
                  },
                })
              }
              onDismiss={() => setDialerBanner(null)}
            />
          ) : null}

          {/* Lead Identity & Primary Calling Actions Card */}
          <View style={styles.heroCard}>
            <View style={styles.heroTopRow}>
              <Text style={styles.leadName} selectable>
                {lead.name}
              </Text>
              <LeadStatusBadge status={lead.status} />
            </View>
            <Text style={styles.leadPrimaryPhone} selectable>
              {lead.phone}
            </Text>
            {lead.course_interest ? (
              <View style={styles.courseTag}>
                <Text style={styles.courseTagText}>{lead.course_interest}</Text>
              </View>
            ) : null}

            {/* Primary Click-to-Call Button */}
            <View style={styles.callActionsBlock}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Call ${lead.name} at ${lead.phone}`}
                onPress={() => void handleClickToCall(lead.phone)}
                style={({ pressed }) => [
                  styles.callButton,
                  pressed && styles.buttonPressed,
                ]}
              >
                <Text style={styles.callButtonText}>
                  Call {lead.phone || 'Lead'}
                </Text>
              </Pressable>

              {lead.alternate_phone ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Call alternate number ${lead.alternate_phone}`}
                  onPress={() => void handleClickToCall(lead.alternate_phone)}
                  style={({ pressed }) => [
                    styles.altCallButton,
                    pressed && styles.buttonPressed,
                  ]}
                >
                  <Text style={styles.altCallButtonText}>
                    Call Alternate ({lead.alternate_phone})
                  </Text>
                </Pressable>
              ) : null}

              <View style={styles.workflowActionsRow}>
                <Pressable
                  accessibilityRole="button"
                  onPress={() =>
                    router.push({
                      pathname: '/log-call',
                      params: {
                        leadId: String(lead.id),
                        leadName: lead.name,
                        leadPhone: lead.phone,
                        dialerOpenedAt: dialerBanner?.openedAtIso,
                      },
                    })
                  }
                  style={({ pressed }) => [
                    styles.workflowPrimaryBtn,
                    pressed && styles.buttonPressed,
                  ]}
                >
                  <Text style={styles.workflowPrimaryBtnText}>
                    Log Call Outcome
                  </Text>
                </Pressable>

                <Pressable
                  accessibilityRole="button"
                  onPress={() =>
                    router.push({
                      pathname: '/schedule-followup',
                      params: {
                        mode: 'create',
                        leadId: String(lead.id),
                        leadName: lead.name,
                      },
                    })
                  }
                  style={({ pressed }) => [
                    styles.workflowSecondaryBtn,
                    pressed && styles.buttonPressed,
                  ]}
                >
                  <Text style={styles.workflowSecondaryBtnText}>
                    Schedule Follow-Up
                  </Text>
                </Pressable>
              </View>
            </View>
          </View>

          {/* Pending Follow-Ups for This Lead */}
          <View style={styles.sectionCard}>
            <View style={styles.sectionHeaderRow}>
              <Text style={styles.sectionTitle}>
                Pending Follow-Ups ({pendingFollowUps.length})
              </Text>
              <Pressable
                accessibilityRole="button"
                onPress={() =>
                  router.push({
                    pathname: '/schedule-followup',
                    params: {
                      mode: 'create',
                      leadId: String(lead.id),
                      leadName: lead.name,
                    },
                  })
                }
              >
                <Text style={styles.sectionActionLink}>+ Add</Text>
              </Pressable>
            </View>

            {pendingFollowUps.length === 0 ? (
              <Text style={styles.emptySectionText}>
                No pending follow-ups scheduled for this lead.
              </Text>
            ) : (
              <View style={styles.subList}>
                {pendingFollowUps.map((fu) => (
                  <View key={fu.id} style={styles.subCard}>
                    <Text style={styles.subCardTitle}>
                      Due: {formatDateTime(fu.scheduled_at)}
                    </Text>
                    {fu.notes ? (
                      <Text style={styles.subCardNotes}>{fu.notes}</Text>
                    ) : null}
                    <View style={styles.subCardActions}>
                      <Pressable
                        accessibilityRole="button"
                        onPress={() =>
                          router.push({
                            pathname: '/schedule-followup',
                            params: {
                              mode: 'reschedule',
                              followUpId: String(fu.id),
                              leadId: String(lead.id),
                              leadName: lead.name,
                              initialScheduledAt: fu.scheduled_at,
                              initialNotes: fu.notes,
                            },
                          })
                        }
                        style={styles.miniActionBtn}
                      >
                        <Text style={styles.miniActionBtnText}>Reschedule</Text>
                      </Pressable>

                      <Pressable
                        accessibilityRole="button"
                        disabled={completingFollowUpId === fu.id}
                        onPress={() => handleCompleteFollowUp(fu)}
                        style={styles.miniCompleteBtn}
                      >
                        <Text style={styles.miniCompleteBtnText}>
                          {completingFollowUpId === fu.id
                            ? 'Saving…'
                            : 'Complete'}
                        </Text>
                      </Pressable>
                    </View>
                  </View>
                ))}
              </View>
            )}
          </View>

          {/* Recent Call History for This Lead */}
          <View style={styles.sectionCard}>
            <View style={styles.sectionHeaderRow}>
              <Text style={styles.sectionTitle}>Recent Calls with This Lead</Text>
              <Pressable
                accessibilityRole="button"
                onPress={() => router.push('/(tabs)/history')}
              >
                <Text style={styles.sectionActionLink}>All Calls ›</Text>
              </Pressable>
            </View>

            {recentCalls.length === 0 ? (
              <Text style={styles.emptySectionText}>
                No calls logged by you for this lead yet.
              </Text>
            ) : (
              <View style={styles.subList}>
                {recentCalls.map((call) => (
                  <View key={call.id} style={styles.subCard}>
                    <View style={styles.callRowTop}>
                      <Text style={styles.callOutcomeText}>
                        {call.outcome_label_snapshot || call.outcome_code_snapshot}
                      </Text>
                      <Text style={styles.callDurationText}>
                        {formatDurationSeconds(call.duration_seconds)}
                      </Text>
                    </View>
                    <Text style={styles.callDateText}>
                      {formatDateTime(call.started_at)}
                    </Text>
                    {call.employee_notes ? (
                      <Text style={styles.subCardNotes}>
                        {call.employee_notes}
                      </Text>
                    ) : null}
                  </View>
                ))}
              </View>
            )}
          </View>

          {/* Contact & Course Information */}
          <View style={styles.sectionCard}>
            <Text style={styles.sectionTitle}>Contact & Course Details</Text>
            <DetailField
              label="Primary Phone"
              value={lead.phone}
              colors={colors}
            />
            <DetailField
              label="Alternate Phone"
              value={lead.alternate_phone || 'Not provided'}
              colors={colors}
            />
            <DetailField
              label="Email Address"
              value={lead.email || 'Not provided'}
              colors={colors}
            />
            <DetailField
              label="Course / Service Interest"
              value={lead.course_interest || 'Not specified'}
              colors={colors}
            />
          </View>

          {/* Enquiry Details */}
          <View style={styles.sectionCard}>
            <Text style={styles.sectionTitle}>Enquiry Details</Text>
            <Text style={styles.enquiryBody} selectable>
              {lead.enquiry_details?.trim() ||
                'No additional enquiry details were recorded for this lead.'}
            </Text>
          </View>

          {/* Record Metadata */}
          <View style={styles.sectionCard}>
            <Text style={styles.sectionTitle}>Record Timestamps</Text>
            <DetailField
              label="Lead ID"
              value={`#${lead.id}`}
              colors={colors}
            />
            <DetailField
              label="Created"
              value={formatDateTime(lead.created_at)}
              colors={colors}
            />
            <DetailField
              label="Last Updated"
              value={formatDateTime(lead.updated_at)}
              colors={colors}
            />
          </View>
        </ScrollView>
      ) : null}
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
    headerTitleCol: {
      flex: 1,
      alignItems: 'center',
      paddingHorizontal: 10,
    },
    headerTitle: {
      fontSize: 17,
      fontWeight: '700',
      color: colors.text,
    },
    headerSubtitle: {
      fontSize: 12,
      color: colors.muted,
      marginTop: 1,
    },
    headerSpacer: {
      width: 40,
    },
    scrollContent: {
      paddingHorizontal: 18,
      paddingTop: 16,
      paddingBottom: 32,
      gap: 16,
    },
    heroCard: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      padding: 18,
      borderWidth: 1,
      borderColor: colors.border,
    },
    heroTopRow: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      justifyContent: 'space-between',
      gap: 12,
    },
    leadName: {
      flex: 1,
      fontSize: 21,
      fontWeight: '800',
      color: colors.text,
    },
    leadPrimaryPhone: {
      fontSize: 16,
      fontWeight: '700',
      color: colors.primary,
      marginTop: 8,
    },
    courseTag: {
      alignSelf: 'flex-start',
      marginTop: 10,
      backgroundColor: colors.surfaceMuted,
      paddingHorizontal: 12,
      paddingVertical: 5,
      borderRadius: 8,
    },
    courseTagText: {
      fontSize: 13,
      fontWeight: '600',
      color: colors.secondary,
    },
    callActionsBlock: {
      marginTop: 16,
      gap: 10,
    },
    callButton: {
      backgroundColor: colors.success,
      borderRadius: 12,
      paddingVertical: 14,
      alignItems: 'center',
      justifyContent: 'center',
    },
    callButtonText: {
      color: '#FFFFFF',
      fontSize: 15.5,
      fontWeight: '800',
      letterSpacing: 0.3,
    },
    altCallButton: {
      backgroundColor: colors.successSoft,
      borderRadius: 12,
      paddingVertical: 11,
      alignItems: 'center',
      justifyContent: 'center',
    },
    altCallButtonText: {
      color: colors.success,
      fontSize: 13.5,
      fontWeight: '700',
    },
    workflowActionsRow: {
      flexDirection: 'row',
      gap: 10,
      marginTop: 2,
    },
    workflowPrimaryBtn: {
      flex: 1,
      backgroundColor: colors.primary,
      borderRadius: 12,
      paddingVertical: 12,
      alignItems: 'center',
      justifyContent: 'center',
    },
    workflowPrimaryBtnText: {
      color: colors.onPrimary,
      fontSize: 13.5,
      fontWeight: '700',
    },
    workflowSecondaryBtn: {
      flex: 1,
      backgroundColor: colors.background,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      paddingVertical: 12,
      alignItems: 'center',
      justifyContent: 'center',
    },
    workflowSecondaryBtnText: {
      color: colors.text,
      fontSize: 13.5,
      fontWeight: '700',
    },
    sectionCard: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      paddingHorizontal: 16,
      paddingTop: 16,
      paddingBottom: 12,
      borderWidth: 1,
      borderColor: colors.border,
    },
    sectionHeaderRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 8,
    },
    sectionTitle: {
      fontSize: 15,
      fontWeight: '700',
      color: colors.text,
      marginBottom: 4,
    },
    sectionActionLink: {
      fontSize: 13,
      fontWeight: '700',
      color: colors.primary,
    },
    emptySectionText: {
      fontSize: 13.5,
      color: colors.muted,
      paddingVertical: 6,
    },
    subList: {
      gap: 10,
      marginTop: 4,
    },
    subCard: {
      backgroundColor: colors.background,
      borderRadius: 12,
      padding: 12,
      borderWidth: 1,
      borderColor: colors.border,
    },
    subCardTitle: {
      fontSize: 13.5,
      fontWeight: '700',
      color: colors.text,
    },
    subCardNotes: {
      fontSize: 13,
      color: colors.secondary,
      marginTop: 4,
      lineHeight: 18,
    },
    subCardActions: {
      flexDirection: 'row',
      gap: 8,
      marginTop: 10,
    },
    miniActionBtn: {
      paddingHorizontal: 11,
      paddingVertical: 6,
      borderRadius: 8,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
    },
    miniActionBtnText: {
      fontSize: 12,
      fontWeight: '600',
      color: colors.text,
    },
    miniCompleteBtn: {
      paddingHorizontal: 11,
      paddingVertical: 6,
      borderRadius: 8,
      backgroundColor: colors.successSoft,
    },
    miniCompleteBtnText: {
      fontSize: 12,
      fontWeight: '700',
      color: colors.success,
    },
    callRowTop: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
    },
    callOutcomeText: {
      fontSize: 14,
      fontWeight: '700',
      color: colors.primary,
    },
    callDurationText: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.secondary,
    },
    callDateText: {
      fontSize: 12,
      color: colors.muted,
      marginTop: 3,
    },
    enquiryBody: {
      fontSize: 14.5,
      lineHeight: 22,
      color: colors.secondary,
      paddingVertical: 8,
    },
    centerState: {
      flex: 1,
      alignItems: 'center',
      justifyContent: 'center',
      paddingHorizontal: 26,
    },
    centerStateTitle: {
      fontSize: 18,
      fontWeight: '700',
      color: colors.text,
      textAlign: 'center',
    },
    centerStateSubtitle: {
      fontSize: 14,
      lineHeight: 20,
      color: colors.muted,
      textAlign: 'center',
      marginTop: 8,
    },
    errorActionsRow: {
      flexDirection: 'row',
      gap: 10,
      marginTop: 18,
    },
    primaryBtn: {
      backgroundColor: colors.primary,
      paddingHorizontal: 18,
      paddingVertical: 11,
      borderRadius: 10,
    },
    primaryBtnText: {
      color: colors.onPrimary,
      fontSize: 14,
      fontWeight: '700',
    },
    secondaryBtn: {
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      paddingHorizontal: 16,
      paddingVertical: 11,
      borderRadius: 10,
    },
    secondaryBtnText: {
      color: colors.text,
      fontSize: 14,
      fontWeight: '600',
    },
    buttonPressed: {
      opacity: 0.84,
    },
  });
