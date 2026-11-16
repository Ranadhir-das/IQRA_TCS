import React, { useCallback, useEffect, useRef, useState } from 'react';
import { router, useFocusEffect } from 'expo-router';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ErrorBanner } from '../../components/ErrorBanner.tsx';
import { DEFAULT_LEADS_PAGE_SIZE } from '../../constants/config.ts';
import type { AppColors } from '../../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../../context/AppThemeContext.tsx';
import { fetchActiveCallOutcomes, fetchMyCallHistory } from '../../services/calls.ts';
import type { CallHistoryItem, CallOutcome } from '../../types/index.ts';
import { formatDateTime, formatDurationSeconds } from '../../utils/format.ts';

export default function MyCallHistoryScreen() {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();

  const [outcomes, setOutcomes] = useState<CallOutcome[]>([]);
  const [selectedOutcomeCode, setSelectedOutcomeCode] = useState<string>('ALL');

  const [calls, setCalls] = useState<CallHistoryItem[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(1);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    void fetchActiveCallOutcomes()
      .then((list) => setOutcomes(list))
      .catch(() => {});
  }, []);

  const loadPage = useCallback(
    async (
      targetPage: number,
      mode: 'initial' | 'refresh' | 'append',
      outcomeFilter: string = selectedOutcomeCode
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
        const response = await fetchMyCallHistory(
          {
            page: targetPage,
            page_size: DEFAULT_LEADS_PAGE_SIZE,
            outcome_code: outcomeFilter === 'ALL' ? undefined : outcomeFilter,
          },
          controller.signal
        );

        if (controller.signal.aborted) return;

        setTotalCount(response.count);
        setHasNextPage(Boolean(response.next));
        setPage(targetPage);

        if (mode === 'append') {
          setCalls((prev) => {
            const seen = new Set(prev.map((c) => c.id));
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
          setCalls(response.results);
        }
        setError(null);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(
          err instanceof Error
            ? err.message
            : 'Unable to load call history. Please try again.'
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
    [selectedOutcomeCode]
  );

  useFocusEffect(
    useCallback(() => {
      void loadPage(1, 'initial', selectedOutcomeCode);
      return () => {
        activeControllerRef.current?.abort();
      };
    }, [loadPage, selectedOutcomeCode])
  );

  const renderCallItem = ({ item }: { item: CallHistoryItem }) => (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Open lead ${item.lead_name}`}
      onPress={() =>
        router.push({
          pathname: '/lead-details',
          params: { id: String(item.lead_id) },
        })
      }
      style={({ pressed }) => [
        styles.card,
        pressed && styles.cardPressed,
      ]}
    >
      <View style={styles.cardTopRow}>
        <Text style={styles.leadName} numberOfLines={1}>
          {item.lead_name || `Lead #${item.lead_id}`}
        </Text>
        <View style={styles.outcomeBadge}>
          <Text style={styles.outcomeBadgeText} numberOfLines={1}>
            {item.outcome_label_snapshot || item.outcome_code_snapshot}
          </Text>
        </View>
      </View>

      <View style={styles.metaRow}>
        <Text style={styles.metaText}>{formatDateTime(item.started_at)}</Text>
        <View style={styles.durationPill}>
          <Text style={styles.durationPillText}>
            Duration: {formatDurationSeconds(item.duration_seconds)}
          </Text>
        </View>
      </View>

      {item.employee_notes ? (
        <Text style={styles.notesText}>{item.employee_notes}</Text>
      ) : null}

      <View style={styles.cardFooterRow}>
        <Text style={styles.outcomeCodeSubtext}>
          Code: {item.outcome_code_snapshot}
        </Text>
        <Text style={styles.openLeadHint}>View Lead ›</Text>
      </View>
    </Pressable>
  );

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.headerBlock}>
        <View style={styles.titleRow}>
          <View>
            <Text style={styles.screenTitle}>My Call History</Text>
            <Text style={styles.screenSubtitle}>
              {loading && calls.length === 0
                ? 'Loading your logged calls…'
                : `${totalCount} ${totalCount === 1 ? 'call' : 'calls'} recorded`}
            </Text>
          </View>
          {selectedOutcomeCode !== 'ALL' ? (
            <Pressable
              accessibilityRole="button"
              onPress={() => setSelectedOutcomeCode('ALL')}
              style={styles.resetPill}
            >
              <Text style={styles.resetPillText}>Show All</Text>
            </Pressable>
          ) : null}
        </View>

        {outcomes.length > 0 ? (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.filterChipsRow}
          >
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ selected: selectedOutcomeCode === 'ALL' }}
              onPress={() => setSelectedOutcomeCode('ALL')}
              style={[
                styles.filterChip,
                selectedOutcomeCode === 'ALL' && styles.filterChipSelected,
              ]}
            >
              <Text
                style={[
                  styles.filterChipText,
                  selectedOutcomeCode === 'ALL' && styles.filterChipTextSelected,
                ]}
              >
                All Outcomes
              </Text>
            </Pressable>
            {outcomes.map((o) => {
              const selected = selectedOutcomeCode === o.code;
              return (
                <Pressable
                  key={o.id}
                  accessibilityRole="button"
                  accessibilityState={{ selected }}
                  onPress={() => setSelectedOutcomeCode(o.code)}
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
                    {o.label}
                  </Text>
                </Pressable>
              );
            })}
          </ScrollView>
        ) : null}
      </View>

      {error ? (
        <View style={styles.bannerWrap}>
          <ErrorBanner
            message={error}
            actionLabel="Retry"
            onAction={() => void loadPage(1, 'refresh', selectedOutcomeCode)}
          />
        </View>
      ) : null}

      {loading && calls.length === 0 ? (
        <View style={styles.centerState}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.centerStateSubtitle}>
            Loading your call history…
          </Text>
        </View>
      ) : (
        <FlatList
          data={calls}
          keyExtractor={(item) => String(item.id)}
          renderItem={renderCallItem}
          contentContainerStyle={
            calls.length === 0 ? styles.emptyListContent : styles.listContent
          }
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => void loadPage(1, 'refresh', selectedOutcomeCode)}
              tintColor={colors.primary}
            />
          }
          onEndReachedThreshold={0.35}
          onEndReached={() => {
            if (hasNextPage && !loading && !refreshing && !loadingMore) {
              void loadPage(page + 1, 'append', selectedOutcomeCode);
            }
          }}
          ListEmptyComponent={
            <View style={styles.centerState}>
              <Text style={styles.centerStateTitle}>
                {selectedOutcomeCode !== 'ALL'
                  ? 'No calls with this outcome'
                  : 'No calls logged yet'}
              </Text>
              <Text style={styles.centerStateSubtitle}>
                {selectedOutcomeCode !== 'ALL'
                  ? 'Try selecting another call outcome filter.'
                  : 'Calls you log from your assigned leads will appear here in chronological order.'}
              </Text>
            </View>
          }
          ListFooterComponent={
            loadingMore ? (
              <View style={styles.footerLoader}>
                <ActivityIndicator size="small" color={colors.primary} />
                <Text style={styles.footerText}>Loading more calls…</Text>
              </View>
            ) : calls.length > 0 ? (
              <Text style={styles.footerText}>
                Showing {calls.length} of {totalCount} logged{' '}
                {totalCount === 1 ? 'call' : 'calls'}
              </Text>
            ) : null
          }
        />
      )}
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
    titleRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 10,
    },
    screenTitle: {
      fontSize: 22,
      fontWeight: '800',
      color: colors.text,
    },
    screenSubtitle: {
      fontSize: 13,
      color: colors.muted,
      marginTop: 2,
    },
    resetPill: {
      paddingHorizontal: 12,
      paddingVertical: 6,
      borderRadius: 999,
      backgroundColor: colors.accentSoft,
    },
    resetPillText: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.primary,
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
    cardPressed: {
      opacity: 0.84,
    },
    cardTopRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 10,
    },
    leadName: {
      flex: 1,
      fontSize: 16.5,
      fontWeight: '700',
      color: colors.text,
    },
    outcomeBadge: {
      backgroundColor: colors.accentSoft,
      paddingHorizontal: 10,
      paddingVertical: 4,
      borderRadius: 999,
      maxWidth: '52%',
    },
    outcomeBadgeText: {
      fontSize: 11.5,
      fontWeight: '700',
      color: colors.primary,
    },
    metaRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginTop: 8,
      gap: 10,
    },
    metaText: {
      fontSize: 13,
      color: colors.secondary,
      fontWeight: '500',
    },
    durationPill: {
      backgroundColor: colors.surfaceMuted,
      paddingHorizontal: 9,
      paddingVertical: 3,
      borderRadius: 8,
    },
    durationPillText: {
      fontSize: 12,
      fontWeight: '700',
      color: colors.secondary,
    },
    notesText: {
      marginTop: 10,
      fontSize: 13.5,
      lineHeight: 19,
      color: colors.secondary,
      backgroundColor: colors.surfaceMuted,
      paddingHorizontal: 10,
      paddingVertical: 8,
      borderRadius: 10,
    },
    cardFooterRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginTop: 10,
      paddingTop: 8,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: colors.border,
    },
    outcomeCodeSubtext: {
      fontSize: 11.5,
      color: colors.muted,
    },
    openLeadHint: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.primary,
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
  });
