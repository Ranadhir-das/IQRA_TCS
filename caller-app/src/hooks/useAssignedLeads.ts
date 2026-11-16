import { useCallback, useEffect, useRef, useState } from 'react';
import { DEFAULT_LEADS_PAGE_SIZE } from '../constants/config.ts';
import { fetchAssignedLeads } from '../services/leads.ts';
import type { EmployeeLead, LeadStatusFilter } from '../types/index.ts';

export interface UseAssignedLeadsOptions {
  search?: string;
  statusFilter?: LeadStatusFilter;
  pageSize?: number;
  enabled?: boolean;
}

export interface UseAssignedLeadsResult {
  leads: EmployeeLead[];
  totalCount: number;
  page: number;
  hasNextPage: boolean;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  loadNextPage: () => Promise<void>;
}

export function useAssignedLeads({
  search = '',
  statusFilter = 'ALL',
  pageSize = DEFAULT_LEADS_PAGE_SIZE,
  enabled = true,
}: UseAssignedLeadsOptions = {}): UseAssignedLeadsResult {
  const [leads, setLeads] = useState<EmployeeLead[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(1);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeControllerRef = useRef<AbortController | null>(null);
  const loadingMoreRef = useRef(false);

  const loadPage = useCallback(
    async (targetPage: number, mode: 'initial' | 'refresh' | 'append') => {
      if (!enabled) return;

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
        loadingMoreRef.current = true;
        setLoadingMore(true);
      }

      try {
        const response = await fetchAssignedLeads(
          {
            page: targetPage,
            page_size: pageSize,
            search: search.trim() || undefined,
            status: statusFilter === 'ALL' ? undefined : statusFilter,
          },
          controller.signal
        );

        if (controller.signal.aborted) return;

        setTotalCount(response.count);
        setHasNextPage(Boolean(response.next));
        setPage(targetPage);

        if (mode === 'append') {
          setLeads((previous) => {
            const seen = new Set(previous.map((item) => item.id));
            const merged = [...previous];
            for (const item of response.results) {
              if (!seen.has(item.id)) {
                seen.add(item.id);
                merged.push(item);
              }
            }
            return merged;
          });
        } else {
          setLeads(response.results);
        }
        setError(null);
      } catch (err) {
        if (controller.signal.aborted) return;
        const message =
          err instanceof Error
            ? err.message
            : 'Unable to load assigned leads. Please try again.';
        setError(message);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          setRefreshing(false);
        }
        if (mode === 'append') {
          loadingMoreRef.current = false;
          setLoadingMore(false);
        }
      }
    },
    [enabled, pageSize, search, statusFilter]
  );

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    void loadPage(1, 'initial');
    return () => {
      activeControllerRef.current?.abort();
    };
  }, [enabled, loadPage]);

  const refresh = useCallback(async () => {
    await loadPage(1, 'refresh');
  }, [loadPage]);

  const loadNextPage = useCallback(async () => {
    if (!hasNextPage || loading || refreshing || loadingMoreRef.current) {
      return;
    }
    await loadPage(page + 1, 'append');
  }, [hasNextPage, loading, refreshing, loadPage, page]);

  return {
    leads,
    totalCount,
    page,
    hasNextPage,
    loading,
    refreshing,
    loadingMore,
    error,
    refresh,
    loadNextPage,
  };
}
