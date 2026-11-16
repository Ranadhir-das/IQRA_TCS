import type { LeadStatus, LeadStatusFilter } from '../types/index.ts';

export interface LeadStatusMeta {
  value: LeadStatus;
  label: string;
  shortLabel: string;
  tone: 'info' | 'primary' | 'success' | 'warning' | 'danger' | 'neutral';
}

export const LEAD_STATUS_META: Record<LeadStatus, LeadStatusMeta> = {
  NEW: {
    value: 'NEW',
    label: 'New',
    shortLabel: 'New',
    tone: 'primary',
  },
  CONTACTED: {
    value: 'CONTACTED',
    label: 'Contacted',
    shortLabel: 'Contacted',
    tone: 'info',
  },
  INTERESTED: {
    value: 'INTERESTED',
    label: 'Interested',
    shortLabel: 'Interested',
    tone: 'success',
  },
  NOT_INTERESTED: {
    value: 'NOT_INTERESTED',
    label: 'Not Interested',
    shortLabel: 'Not Interested',
    tone: 'danger',
  },
  CONVERTED: {
    value: 'CONVERTED',
    label: 'Converted',
    shortLabel: 'Converted',
    tone: 'success',
  },
};

export const LEAD_STATUS_FILTERS: { id: LeadStatusFilter; label: string }[] = [
  { id: 'ALL', label: 'All Assigned' },
  { id: 'NEW', label: 'New' },
  { id: 'CONTACTED', label: 'Contacted' },
  { id: 'INTERESTED', label: 'Interested' },
  { id: 'CONVERTED', label: 'Converted' },
  { id: 'NOT_INTERESTED', label: 'Not Interested' },
];

export function getLeadStatusLabel(status: string): string {
  if (status in LEAD_STATUS_META) {
    return LEAD_STATUS_META[status as LeadStatus].label;
  }
  return status || 'Unknown';
}
