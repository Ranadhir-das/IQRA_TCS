import React, { useEffect, useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import {
  ActivityIndicator,
  FlatList,
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
import { LeadStatusBadge } from '../../components/LeadStatusBadge.tsx';
import { LEAD_STATUS_FILTERS, LEAD_STATUS_META } from '../../constants/leads.ts';
import type { AppColors } from '../../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../../context/AppThemeContext.tsx';
import { useAssignedLeads } from '../../hooks/useAssignedLeads.ts';
import { useDebouncedValue } from '../../hooks/useDebouncedValue.ts';
import type { EmployeeLead, LeadStatus, LeadStatusFilter } from '../../types/index.ts';
import { formatRelativeTime } from '../../utils/format.ts';

function isValidStatusFilter(value?: string): value is LeadStatusFilter {
  if (!value) return false;
  if (value === 'ALL') return true;
  return value in LEAD_STATUS_META;
}

export default function AssignedLeadsScreen() {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();
  const params = useLocalSearchParams<{ status?: string }>();

  const [searchInput, setSearchInput] = useState('');
  const [statusFilter, setStatusFilter] = useState<LeadStatusFilter>(() =>
    isValidStatusFilter(params.status) ? params.status : 'ALL'
  );

  useEffect(() => {
    if (isValidStatusFilter(params.status)) {
      setStatusFilter(params.status);
    }
  }, [params.status]);

  const debouncedSearch = useDebouncedValue(searchInput, 300);

  const {
    leads,
    totalCount,
    hasNextPage,
    loading,
    refreshing,
    loadingMore,
    error,
    refresh,
    loadNextPage,
  } = useAssignedLeads({
    search: debouncedSearch,
    statusFilter,
  });

  const hasActiveFilters = Boolean(searchInput.trim()) || statusFilter !== 'ALL';

  const clearFilters = () => {
    setSearchInput('');
    setStatusFilter('ALL');
  };

  const renderLeadItem = ({ item }: { item: EmployeeLead }) => (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`View details for ${item.name}`}
      onPress={() =>
        router.push({
          pathname: '/lead-details',
          params: { id: String(item.id) },
        })
      }
      style={({ pressed }) => [
        styles.leadCard,
        pressed && styles.leadCardPressed,
      ]}
    >
      <View style={styles.cardHeaderRow}>
        <Text style={styles.leadName} numberOfLines={1}>
          {item.name}
        </Text>
        <LeadStatusBadge status={item.status} compact />
      </View>

      <View style={styles.phoneRow}>
        <Text style={styles.primaryPhone}>{item.phone}</Text>
        {item.alternate_phone ? (
          <Text style={styles.altPhone} numberOfLines={1}>
            · Alt: {item.alternate_phone}
          </Text>
        ) : null}
      </View>

      <View style={styles.metaRow}>
        <View style={styles.coursePill}>
          <Text style={styles.coursePillText} numberOfLines={1}>
            {item.course_interest || 'General Enquiry'}
          </Text>
        </View>
        <Text style={styles.timestampText}>
          Updated {formatRelativeTime(item.updated_at || item.created_at)}
        </Text>
      </View>

      {item.enquiry_details ? (
        <Text style={styles.enquirySnippet} numberOfLines={2}>
          {item.enquiry_details}
        </Text>
      ) : null}
    </Pressable>
  );

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.headerBlock}>
        <View style={styles.titleRow}>
          <View>
            <Text style={styles.screenTitle}>Assigned Leads</Text>
            <Text style={styles.screenSubtitle}>
              {loading && leads.length === 0
                ? 'Loading your assigned leads…'
                : `${totalCount} ${totalCount === 1 ? 'lead' : 'leads'} matching`}
            </Text>
          </View>
          {hasActiveFilters ? (
            <Pressable
              accessibilityRole="button"
              onPress={clearFilters}
              style={styles.resetButton}
            >
              <Text style={styles.resetButtonText}>Reset Filters</Text>
            </Pressable>
          ) : null}
        </View>

        {/* Search Input */}
        <View style={styles.searchBox}>
          <TextInput
            value={searchInput}
            onChangeText={setSearchInput}
            placeholder="Search by name, phone, or email…"
            placeholderTextColor={colors.placeholder}
            autoCapitalize="none"
            autoCorrect={false}
            returnKeyType="search"
            style={styles.searchInput}
          />
          {searchInput ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Clear search"
              onPress={() => setSearchInput('')}
              style={styles.clearSearchButton}
            >
              <Text style={styles.clearSearchText}>✕</Text>
            </Pressable>
          ) : null}
        </View>

        {/* Status Filter Chips */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.filterChipsRow}
        >
          {LEAD_STATUS_FILTERS.map((filterItem) => {
            const selected = statusFilter === filterItem.id;
            return (
              <Pressable
                key={filterItem.id}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                onPress={() => setStatusFilter(filterItem.id as LeadStatus | 'ALL')}
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
                  {filterItem.label}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      {error ? (
        <View style={styles.errorWrap}>
          <ErrorBanner
            message={error}
            actionLabel="Retry"
            onAction={() => void refresh()}
          />
        </View>
      ) : null}

      {loading && leads.length === 0 ? (
        <View style={styles.centerState}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.centerStateSubtitle}>
            Loading assigned leads…
          </Text>
        </View>
      ) : (
        <FlatList
          data={leads}
          keyExtractor={(item) => String(item.id)}
          renderItem={renderLeadItem}
          contentContainerStyle={
            leads.length === 0 ? styles.emptyListContent : styles.listContent
          }
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => void refresh()}
              tintColor={colors.primary}
            />
          }
          onEndReachedThreshold={0.35}
          onEndReached={() => {
            if (hasNextPage) {
              void loadNextPage();
            }
          }}
          ListEmptyComponent={
            <View style={styles.centerState}>
              <Text style={styles.centerStateTitle}>
                {hasActiveFilters
                  ? 'No matching assigned leads'
                  : 'No leads assigned to you yet'}
              </Text>
              <Text style={styles.centerStateSubtitle}>
                {hasActiveFilters
                  ? 'Try adjusting your search query or status filter.'
                  : 'When an administrator assigns leads to your account, they will appear here.'}
              </Text>
              {hasActiveFilters ? (
                <Pressable
                  accessibilityRole="button"
                  onPress={clearFilters}
                  style={styles.emptyActionBtn}
                >
                  <Text style={styles.emptyActionBtnText}>Clear Filters</Text>
                </Pressable>
              ) : (
                <Pressable
                  accessibilityRole="button"
                  onPress={() => void refresh()}
                  style={styles.emptyActionBtn}
                >
                  <Text style={styles.emptyActionBtnText}>Refresh</Text>
                </Pressable>
              )}
            </View>
          }
          ListFooterComponent={
            loadingMore ? (
              <View style={styles.footerLoader}>
                <ActivityIndicator size="small" color={colors.primary} />
                <Text style={styles.footerLoaderText}>Loading more leads…</Text>
              </View>
            ) : leads.length > 0 ? (
              <Text style={styles.footerCountText}>
                Showing {leads.length} of {totalCount} assigned{' '}
                {totalCount === 1 ? 'lead' : 'leads'}
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
      backgroundColor: colors.background,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    titleRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 12,
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
    resetButton: {
      paddingHorizontal: 12,
      paddingVertical: 6,
      borderRadius: 999,
      backgroundColor: colors.accentSoft,
    },
    resetButtonText: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.primary,
    },
    searchBox: {
      position: 'relative',
      justifyContent: 'center',
      marginBottom: 10,
    },
    searchInput: {
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      paddingHorizontal: 14,
      paddingVertical: 11,
      paddingRight: 40,
      fontSize: 14.5,
      color: colors.text,
    },
    clearSearchButton: {
      position: 'absolute',
      right: 8,
      width: 30,
      height: 30,
      alignItems: 'center',
      justifyContent: 'center',
    },
    clearSearchText: {
      fontSize: 14,
      fontWeight: '700',
      color: colors.muted,
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
    errorWrap: {
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
    leadCard: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      padding: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    leadCardPressed: {
      opacity: 0.82,
    },
    cardHeaderRow: {
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
    phoneRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      marginTop: 6,
    },
    primaryPhone: {
      fontSize: 14.5,
      fontWeight: '600',
      color: colors.secondary,
    },
    altPhone: {
      fontSize: 13,
      color: colors.muted,
      flexShrink: 1,
    },
    metaRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginTop: 10,
      gap: 10,
    },
    coursePill: {
      backgroundColor: colors.surfaceMuted,
      paddingHorizontal: 10,
      paddingVertical: 4,
      borderRadius: 8,
      maxWidth: '62%',
    },
    coursePillText: {
      fontSize: 12,
      fontWeight: '600',
      color: colors.secondary,
    },
    timestampText: {
      fontSize: 12,
      color: colors.muted,
    },
    enquirySnippet: {
      marginTop: 10,
      fontSize: 13,
      lineHeight: 18,
      color: colors.muted,
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
    emptyActionBtn: {
      marginTop: 16,
      backgroundColor: colors.primary,
      paddingHorizontal: 18,
      paddingVertical: 11,
      borderRadius: 10,
    },
    emptyActionBtnText: {
      color: colors.onPrimary,
      fontSize: 13.5,
      fontWeight: '700',
    },
    footerLoader: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
      paddingVertical: 16,
    },
    footerLoaderText: {
      fontSize: 13,
      color: colors.muted,
    },
    footerCountText: {
      textAlign: 'center',
      fontSize: 12.5,
      color: colors.muted,
      paddingVertical: 14,
    },
  });
