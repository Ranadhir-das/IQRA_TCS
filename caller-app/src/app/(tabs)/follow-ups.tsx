import React, { useCallback, useEffect, useRef, useState } from 'react';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ErrorBanner } from '../../components/ErrorBanner.tsx';
import { DEFAULT_LEADS_PAGE_SIZE } from '../../constants/config.ts';
import type { AppColors } from '../../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../../context/AppThemeContext.tsx';
import { openNativeDialer } from '../../services/dialer.ts';
import {
  MAX_FOLLOWUP_REASON_LENGTH,
  cancelFollowUp,
  completeFollowUp,
  fetchMyFollowUps,
  getFollowUpTimingCategory,
} from '../../services/followUps.ts';
import type { EmployeeFollowUp, FollowUpListQueryParams } from '../../types/index.ts';
import { formatDateTime } from '../../utils/format.ts';

type FollowUpBucketFilter =
  | 'ALL_PENDING'
  | 'TODAY'
  | 'OVERDUE'
  | 'UPCOMING'
  | 'COMPLETED'
  | 'CANCELLED';

const BUCKET_FILTERS: { id: FollowUpBucketFilter; label: string }[] = [
  { id: 'ALL_PENDING', label: 'Pending' },
  { id: 'TODAY', label: 'Today' },
  { id: 'OVERDUE', label: 'Overdue' },
  { id: 'UPCOMING', label: 'Upcoming' },
  { id: 'COMPLETED', label: 'Completed' },
  { id: 'CANCELLED', label: 'Cancelled' },
];

function buildQueryFromBucket(
  bucket: FollowUpBucketFilter,
  page: number
): FollowUpListQueryParams {
  const base: FollowUpListQueryParams = {
    page,
    page_size: DEFAULT_LEADS_PAGE_SIZE,
  };
  switch (bucket) {
    case 'TODAY':
      return { ...base, due_state: 'today' };
    case 'OVERDUE':
      return { ...base, due_state: 'overdue' };
    case 'UPCOMING':
      return { ...base, due_state: 'upcoming' };
    case 'COMPLETED':
      return { ...base, status: 'COMPLETED' };
    case 'CANCELLED':
      return { ...base, status: 'CANCELLED' };
    default:
      return { ...base, status: 'PENDING' };
  }
}

export default function MyFollowUpsScreen() {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();
  const params = useLocalSearchParams<{ bucket?: string }>();

  const [bucket, setBucket] = useState<FollowUpBucketFilter>(() => {
    const incoming = params.bucket?.toUpperCase() as FollowUpBucketFilter;
    return BUCKET_FILTERS.some((b) => b.id === incoming)
      ? incoming
      : 'ALL_PENDING';
  });

  useEffect(() => {
    const incoming = params.bucket?.toUpperCase() as FollowUpBucketFilter;
    if (BUCKET_FILTERS.some((b) => b.id === incoming)) {
      setBucket(incoming);
    }
  }, [params.bucket]);

  const [items, setItems] = useState<EmployeeFollowUp[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(1);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const [summaryCounts, setSummaryCounts] = useState({
    pending: 0,
    today: 0,
    overdue: 0,
    upcoming: 0,
  });

  const [busyFollowUpId, setBusyFollowUpId] = useState<number | null>(null);
  const [cancelTarget, setCancelTarget] = useState<EmployeeFollowUp | null>(null);
  const [cancelReason, setCancelReason] = useState('');

  const activeControllerRef = useRef<AbortController | null>(null);

  const loadSummaryCounts = useCallback(async () => {
    try {
      const [pendingRes, todayRes, overdueRes, upcomingRes] = await Promise.all([
        fetchMyFollowUps(
          { status: 'PENDING', page: 1, page_size: 1 },
          { enrichLeadPhones: false }
        ),
        fetchMyFollowUps(
          { due_state: 'today', page: 1, page_size: 1 },
          { enrichLeadPhones: false }
        ),
        fetchMyFollowUps(
          { due_state: 'overdue', page: 1, page_size: 1 },
          { enrichLeadPhones: false }
        ),
        fetchMyFollowUps(
          { due_state: 'upcoming', page: 1, page_size: 1 },
          { enrichLeadPhones: false }
        ),
      ]);
      setSummaryCounts({
        pending: pendingRes.count,
        today: todayRes.count,
        overdue: overdueRes.count,
        upcoming: upcomingRes.count,
      });
    } catch {
      // Summary count errors are surfaced by the primary list request.
    }
  }, []);

  const loadPage = useCallback(
    async (
      targetPage: number,
      mode: 'initial' | 'refresh' | 'append',
      selectedBucket: FollowUpBucketFilter = bucket
    ) => {
      if (mode !== 'append') {
        activeControllerRef.current?.abort();
      }
      const controller = new AbortController();
      if (mode !== 'append') {
        activeControllerRef.current = controller;
      }

      if (mode === 'initial') {
        setLoading(true);
        setError(null);
      } else if (mode === 'refresh') {
        setRefreshing(true);
        setError(null);
      } else {
        setLoadingMore(true);
      }

      try {
        const response = await fetchMyFollowUps(
          buildQueryFromBucket(selectedBucket, targetPage),
          { signal: controller.signal, enrichLeadPhones: true }
        );
        if (controller.signal.aborted) return;

        setTotalCount(response.count);
        setHasNextPage(Boolean(response.next));
        setPage(targetPage);

        if (mode === 'append') {
          setItems((prev) => {
            const seen = new Set(prev.map((i) => i.id));
            const merged = [...prev];
            for (const item of response.results) {
              if (!seen.has(item.id)) {
                seen.add(item.id);
                merged.push(item);
              }
            }
            return merged;
          });
        } else {
          setItems(response.results);
        }
        setError(null);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(
          err instanceof Error
            ? err.message
            : 'Unable to load follow-ups. Please try again.'
        );
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          setRefreshing(false);
        }
        if (mode === 'append') {
          setLoadingMore(false);
        }
      }
    },
    [bucket]
  );

  useFocusEffect(
    useCallback(() => {
      void loadSummaryCounts();
      void loadPage(1, 'initial', bucket);
      return () => {
        activeControllerRef.current?.abort();
      };
    }, [bucket, loadPage, loadSummaryCounts])
  );

  const handleRefresh = async () => {
    setActionSuccess(null);
    await Promise.all([loadSummaryCounts(), loadPage(1, 'refresh', bucket)]);
  };

  const handleCompleteFollowUp = (item: EmployeeFollowUp) => {
    if (busyFollowUpId !== null) return;
    Alert.alert(
      'Complete Follow-Up',
      `Mark follow-up for ${item.lead_name || `Lead #${item.lead_id}`} as completed?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Mark Completed',
          onPress: () => {
            setBusyFollowUpId(item.id);
            setError(null);
            setActionSuccess(null);
            void completeFollowUp(item.id)
              .then(() => {
                setActionSuccess(
                  `Follow-up for ${item.lead_name} marked as completed.`
                );
                return Promise.all([
                  loadSummaryCounts(),
                  loadPage(1, 'refresh', bucket),
                ]);
              })
              .catch((err) => {
                setError(
                  err instanceof Error
                    ? err.message
                    : 'Could not mark follow-up completed.'
                );
              })
              .finally(() => {
                setBusyFollowUpId(null);
              });
          },
        },
      ]
    );
  };

  const handleConfirmCancel = async () => {
    if (!cancelTarget || busyFollowUpId !== null) return;
    const target = cancelTarget;
    const reasonText = cancelReason.trim();
    setBusyFollowUpId(target.id);
    setCancelTarget(null);
    setCancelReason('');
    setError(null);
    setActionSuccess(null);

    try {
      await cancelFollowUp(target.id, reasonText);
      setActionSuccess(`Follow-up for ${target.lead_name} was cancelled.`);
      await Promise.all([
        loadSummaryCounts(),
        loadPage(1, 'refresh', bucket),
      ]);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not cancel follow-up.'
      );
    } finally {
      setBusyFollowUpId(null);
    }
  };

  const handleDialLead = async (item: EmployeeFollowUp) => {
    if (!item.lead_phone) {
      router.push({
        pathname: '/lead-details',
        params: { id: String(item.lead_id) },
      });
      return;
    }
    try {
      await openNativeDialer(item.lead_phone);
    } catch (err) {
      Alert.alert(
        'Cannot Place Call',
        err instanceof Error ? err.message : 'Invalid phone number.'
      );
    }
  };

  const renderTimingBadge = (item: EmployeeFollowUp) => {
    const category = getFollowUpTimingCategory(item);
    const meta = (() => {
      switch (category) {
        case 'overdue':
          return {
            label: 'OVERDUE',
            bg: colors.dangerSoft,
            fg: colors.danger,
          };
        case 'today':
          return {
            label: 'DUE TODAY',
            bg: colors.warningSoft,
            fg: colors.warning,
          };
        case 'completed':
          return {
            label: 'COMPLETED',
            bg: colors.successSoft,
            fg: colors.success,
          };
        case 'cancelled':
          return {
            label: 'CANCELLED',
            bg: colors.neutralSoft,
            fg: colors.neutral,
          };
        default:
          return {
            label: 'UPCOMING',
            bg: colors.infoSoft,
            fg: colors.info,
          };
      }
    })();

    return (
      <View style={[styles.statusBadge, { backgroundColor: meta.bg }]}>
        <Text style={[styles.statusBadgeText, { color: meta.fg }]}>
          {meta.label}
        </Text>
      </View>
    );
  };

  const renderFollowUpItem = ({ item }: { item: EmployeeFollowUp }) => {
    const isBusy = busyFollowUpId === item.id;
    const isPending = item.status === 'PENDING';

    return (
      <View style={styles.card}>
        <View style={styles.cardHeaderRow}>
          <Pressable
            accessibilityRole="button"
            onPress={() =>
              router.push({
                pathname: '/lead-details',
                params: { id: String(item.lead_id) },
              })
            }
            style={{ flex: 1 }}
          >
            <Text style={styles.leadName} numberOfLines={1}>
              {item.lead_name || `Lead #${item.lead_id}`}
            </Text>
            <Text style={styles.leadPhone}>
              {item.lead_phone || `Tap to view Lead #${item.lead_id} phone`}
            </Text>
          </Pressable>
          {renderTimingBadge(item)}
        </View>

        <View style={styles.scheduleRow}>
          <Text style={styles.scheduleLabel}>Scheduled:</Text>
          <Text style={styles.scheduleValue}>
            {formatDateTime(item.scheduled_at)}
          </Text>
        </View>

        {item.call_id ? (
          <Text style={styles.linkedCallText}>
            Linked to Call #{item.call_id}
          </Text>
        ) : null}

        {item.notes ? (
          <Text style={styles.notesText}>{item.notes}</Text>
        ) : null}

        <View style={styles.actionsRow}>
          <Pressable
            accessibilityRole="button"
            onPress={() =>
              router.push({
                pathname: '/lead-details',
                params: { id: String(item.lead_id) },
              })
            }
            style={styles.secondarySmallBtn}
          >
            <Text style={styles.secondarySmallBtnText}>Open Lead</Text>
          </Pressable>

          {item.lead_phone ? (
            <Pressable
              accessibilityRole="button"
              onPress={() => void handleDialLead(item)}
              style={styles.callSmallBtn}
            >
              <Text style={styles.callSmallBtnText}>Call</Text>
            </Pressable>
          ) : null}

          {isPending ? (
            <>
              <Pressable
                accessibilityRole="button"
                disabled={isBusy}
                onPress={() =>
                  router.push({
                    pathname: '/schedule-followup',
                    params: {
                      mode: 'reschedule',
                      followUpId: String(item.id),
                      leadId: String(item.lead_id),
                      leadName: item.lead_name,
                      initialScheduledAt: item.scheduled_at,
                      initialNotes: item.notes,
                    },
                  })
                }
                style={styles.secondarySmallBtn}
              >
                <Text style={styles.secondarySmallBtnText}>Reschedule</Text>
              </Pressable>

              <Pressable
                accessibilityRole="button"
                disabled={isBusy}
                onPress={() => handleCompleteFollowUp(item)}
                style={styles.completeSmallBtn}
              >
                {isBusy ? (
                  <ActivityIndicator size="small" color={colors.success} />
                ) : (
                  <Text style={styles.completeSmallBtnText}>Complete</Text>
                )}
              </Pressable>

              <Pressable
                accessibilityRole="button"
                disabled={isBusy}
                onPress={() => {
                  setCancelTarget(item);
                  setCancelReason('');
                }}
                style={styles.cancelSmallBtn}
              >
                <Text style={styles.cancelSmallBtnText}>Cancel</Text>
              </Pressable>
            </>
          ) : null}
        </View>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.headerBlock}>
        <Text style={styles.screenTitle}>My Follow-Ups</Text>

        {/* Summary Strip */}
        <View style={styles.summaryStrip}>
          <Pressable
            accessibilityRole="button"
            onPress={() => setBucket('OVERDUE')}
            style={[
              styles.summaryTile,
              bucket === 'OVERDUE' && styles.summaryTileActive,
            ]}
          >
            <Text style={[styles.summaryNumber, { color: colors.danger }]}>
              {summaryCounts.overdue}
            </Text>
            <Text style={styles.summaryLabel}>Overdue</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            onPress={() => setBucket('TODAY')}
            style={[
              styles.summaryTile,
              bucket === 'TODAY' && styles.summaryTileActive,
            ]}
          >
            <Text style={[styles.summaryNumber, { color: colors.warning }]}>
              {summaryCounts.today}
            </Text>
            <Text style={styles.summaryLabel}>Today</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            onPress={() => setBucket('UPCOMING')}
            style={[
              styles.summaryTile,
              bucket === 'UPCOMING' && styles.summaryTileActive,
            ]}
          >
            <Text style={[styles.summaryNumber, { color: colors.primary }]}>
              {summaryCounts.upcoming}
            </Text>
            <Text style={styles.summaryLabel}>Upcoming</Text>
          </Pressable>

          <Pressable
            accessibilityRole="button"
            onPress={() => setBucket('ALL_PENDING')}
            style={[
              styles.summaryTile,
              bucket === 'ALL_PENDING' && styles.summaryTileActive,
            ]}
          >
            <Text style={styles.summaryNumber}>{summaryCounts.pending}</Text>
            <Text style={styles.summaryLabel}>All Pending</Text>
          </Pressable>
        </View>

        {/* Filter Chips */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.filterChipsRow}
        >
          {BUCKET_FILTERS.map((f) => {
            const selected = bucket === f.id;
            return (
              <Pressable
                key={f.id}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                onPress={() => {
                  setActionSuccess(null);
                  setBucket(f.id);
                }}
                style={[
                  styles.filterChip,
                  selected && styles.filterChipSelected,
                ]}
              >
                <Text
                  style={[
                    styles.filterChipText,
                    selected && styles.filterChipTextSelected,
                  ]}
                >
                  {f.label}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      {actionSuccess ? (
        <View style={styles.bannerWrap}>
          <ErrorBanner
            variant="success"
            message={actionSuccess}
            onDismiss={() => setActionSuccess(null)}
          />
        </View>
      ) : null}

      {error ? (
        <View style={styles.bannerWrap}>
          <ErrorBanner
            message={error}
            actionLabel="Retry"
            onAction={() => void handleRefresh()}
          />
        </View>
      ) : null}

      {loading && items.length === 0 ? (
        <View style={styles.centerState}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.centerStateSubtitle}>Loading follow-ups…</Text>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => String(item.id)}
          renderItem={renderFollowUpItem}
          contentContainerStyle={
            items.length === 0 ? styles.emptyListContent : styles.listContent
          }
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => void handleRefresh()}
              tintColor={colors.primary}
            />
          }
          onEndReachedThreshold={0.35}
          onEndReached={() => {
            if (hasNextPage && !loading && !refreshing && !loadingMore) {
              void loadPage(page + 1, 'append', bucket);
            }
          }}
          ListEmptyComponent={
            <View style={styles.centerState}>
              <Text style={styles.centerStateTitle}>No follow-ups in this view</Text>
              <Text style={styles.centerStateSubtitle}>
                Schedule follow-ups from any assigned lead or after logging a call.
              </Text>
            </View>
          }
          ListFooterComponent={
            loadingMore ? (
              <View style={styles.footerLoader}>
                <ActivityIndicator size="small" color={colors.primary} />
                <Text style={styles.footerText}>Loading more follow-ups…</Text>
              </View>
            ) : items.length > 0 ? (
              <Text style={styles.footerText}>
                Showing {items.length} of {totalCount}{' '}
                {totalCount === 1 ? 'follow-up' : 'follow-ups'}
              </Text>
            ) : null
          }
        />
      )}

      {/* Cancel Follow-Up Modal */}
      <Modal
        visible={Boolean(cancelTarget)}
        transparent
        animationType="fade"
        onRequestClose={() => setCancelTarget(null)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Cancel Follow-Up</Text>
            <Text style={styles.modalSubtitle}>
              Cancel pending follow-up for{' '}
              {cancelTarget?.lead_name || `Lead #${cancelTarget?.lead_id}`}? This
              action is permanent.
            </Text>

            <Text style={styles.modalFieldLabel}>
              Cancellation Reason (Optional)
            </Text>
            <TextInput
              value={cancelReason}
              onChangeText={setCancelReason}
              placeholder="e.g. Student already enrolled or requested no further callbacks…"
              placeholderTextColor={colors.placeholder}
              multiline
              numberOfLines={3}
              maxLength={MAX_FOLLOWUP_REASON_LENGTH}
              textAlignVertical="top"
              style={styles.modalReasonInput}
            />

            <View style={styles.modalButtonsRow}>
              <Pressable
                accessibilityRole="button"
                onPress={() => setCancelTarget(null)}
                style={styles.modalKeepBtn}
              >
                <Text style={styles.modalKeepBtnText}>Keep Follow-Up</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                onPress={() => void handleConfirmCancel()}
                style={styles.modalConfirmCancelBtn}
              >
                <Text style={styles.modalConfirmCancelBtnText}>
                  Confirm Cancel
                </Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const createStyles = (colors: AppColors) =>
  StyleSheet.create({
    safeArea: {
      flex: 1,
      backgroundColor: colors.background,
    },
    headerBlock: {
      paddingHorizontal: 18,
      paddingTop: 12,
      paddingBottom: 10,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
      backgroundColor: colors.background,
    },
    screenTitle: {
      fontSize: 22,
      fontWeight: '800',
      color: colors.text,
      marginBottom: 12,
    },
    summaryStrip: {
      flexDirection: 'row',
      gap: 8,
      marginBottom: 12,
    },
    summaryTile: {
      flex: 1,
      backgroundColor: colors.surface,
      borderRadius: 12,
      paddingVertical: 9,
      paddingHorizontal: 8,
      alignItems: 'center',
      borderWidth: 1,
      borderColor: colors.border,
    },
    summaryTileActive: {
      borderColor: colors.primary,
      backgroundColor: colors.accentSoft,
    },
    summaryNumber: {
      fontSize: 18,
      fontWeight: '800',
      color: colors.text,
    },
    summaryLabel: {
      fontSize: 11,
      fontWeight: '600',
      color: colors.muted,
      marginTop: 2,
    },
    filterChipsRow: {
      flexDirection: 'row',
      gap: 8,
      paddingVertical: 2,
    },
    filterChip: {
      paddingHorizontal: 13,
      paddingVertical: 7,
      borderRadius: 999,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
    },
    filterChipSelected: {
      backgroundColor: colors.primary,
      borderColor: colors.primary,
    },
    filterChipText: {
      fontSize: 12.5,
      fontWeight: '600',
      color: colors.secondary,
    },
    filterChipTextSelected: {
      color: colors.onPrimary,
      fontWeight: '700',
    },
    bannerWrap: {
      paddingHorizontal: 18,
      paddingTop: 12,
    },
    listContent: {
      paddingHorizontal: 18,
      paddingTop: 14,
      paddingBottom: 28,
      gap: 12,
    },
    emptyListContent: {
      flexGrow: 1,
      paddingHorizontal: 18,
      justifyContent: 'center',
    },
    card: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      padding: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    cardHeaderRow: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      justifyContent: 'space-between',
      gap: 10,
    },
    leadName: {
      fontSize: 16.5,
      fontWeight: '700',
      color: colors.text,
    },
    leadPhone: {
      fontSize: 13.5,
      fontWeight: '600',
      color: colors.primary,
      marginTop: 3,
    },
    statusBadge: {
      paddingHorizontal: 9,
      paddingVertical: 4,
      borderRadius: 999,
    },
    statusBadgeText: {
      fontSize: 10.5,
      fontWeight: '800',
      letterSpacing: 0.4,
    },
    scheduleRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      marginTop: 10,
    },
    scheduleLabel: {
      fontSize: 12.5,
      fontWeight: '600',
      color: colors.muted,
    },
    scheduleValue: {
      fontSize: 13.5,
      fontWeight: '700',
      color: colors.secondary,
    },
    linkedCallText: {
      fontSize: 12,
      color: colors.muted,
      marginTop: 4,
    },
    notesText: {
      marginTop: 8,
      fontSize: 13.5,
      lineHeight: 19,
      color: colors.secondary,
      backgroundColor: colors.surfaceMuted,
      paddingHorizontal: 10,
      paddingVertical: 8,
      borderRadius: 10,
    },
    actionsRow: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 8,
      marginTop: 12,
      paddingTop: 10,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: colors.border,
    },
    secondarySmallBtn: {
      paddingHorizontal: 11,
      paddingVertical: 7,
      borderRadius: 9,
      backgroundColor: colors.background,
      borderWidth: 1,
      borderColor: colors.border,
    },
    secondarySmallBtnText: {
      fontSize: 12.5,
      fontWeight: '600',
      color: colors.text,
    },
    callSmallBtn: {
      paddingHorizontal: 12,
      paddingVertical: 7,
      borderRadius: 9,
      backgroundColor: colors.accentSoft,
    },
    callSmallBtnText: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.primary,
    },
    completeSmallBtn: {
      paddingHorizontal: 12,
      paddingVertical: 7,
      borderRadius: 9,
      backgroundColor: colors.successSoft,
      minWidth: 78,
      alignItems: 'center',
    },
    completeSmallBtnText: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.success,
    },
    cancelSmallBtn: {
      paddingHorizontal: 11,
      paddingVertical: 7,
      borderRadius: 9,
      backgroundColor: colors.dangerSoft,
    },
    cancelSmallBtnText: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.danger,
    },
    centerState: {
      flex: 1,
      alignItems: 'center',
      justifyContent: 'center',
      paddingHorizontal: 24,
      paddingVertical: 40,
    },
    centerStateTitle: {
      fontSize: 17,
      fontWeight: '700',
      color: colors.text,
      textAlign: 'center',
    },
    centerStateSubtitle: {
      fontSize: 13.5,
      lineHeight: 19,
      color: colors.muted,
      textAlign: 'center',
      marginTop: 8,
    },
    footerLoader: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
      paddingVertical: 14,
    },
    footerText: {
      textAlign: 'center',
      fontSize: 12.5,
      color: colors.muted,
      paddingVertical: 14,
    },
    modalBackdrop: {
      flex: 1,
      backgroundColor: 'rgba(8, 18, 30, 0.6)',
      justifyContent: 'center',
      paddingHorizontal: 20,
    },
    modalCard: {
      backgroundColor: colors.surface,
      borderRadius: 18,
      padding: 20,
      borderWidth: 1,
      borderColor: colors.border,
    },
    modalTitle: {
      fontSize: 18,
      fontWeight: '700',
      color: colors.text,
    },
    modalSubtitle: {
      fontSize: 13.5,
      lineHeight: 19,
      color: colors.muted,
      marginTop: 6,
      marginBottom: 14,
    },
    modalFieldLabel: {
      fontSize: 13,
      fontWeight: '600',
      color: colors.secondary,
      marginBottom: 6,
    },
    modalReasonInput: {
      backgroundColor: colors.background,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      paddingHorizontal: 12,
      paddingVertical: 10,
      minHeight: 78,
      fontSize: 14,
      color: colors.text,
      marginBottom: 16,
    },
    modalButtonsRow: {
      flexDirection: 'row',
      gap: 10,
    },
    modalKeepBtn: {
      flex: 1,
      paddingVertical: 12,
      borderRadius: 11,
      borderWidth: 1,
      borderColor: colors.border,
      alignItems: 'center',
      justifyContent: 'center',
    },
    modalKeepBtnText: {
      fontSize: 13.5,
      fontWeight: '600',
      color: colors.text,
    },
    modalConfirmCancelBtn: {
      flex: 1,
      paddingVertical: 12,
      borderRadius: 11,
      backgroundColor: colors.danger,
      alignItems: 'center',
      justifyContent: 'center',
    },
    modalConfirmCancelBtnText: {
      fontSize: 13.5,
      fontWeight: '700',
      color: '#FFFFFF',
    },
  });
