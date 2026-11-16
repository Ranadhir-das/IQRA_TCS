/**
 * Date, time, duration, and display formatting helpers for IQRA TCS Caller App.
 */

export function formatRelativeTime(isoString?: string | null): string {
  if (!isoString) return '—';
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return '—';

  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) {
    return formatDateTime(isoString);
  }

  const diffMinutes = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMinutes < 1) return 'Just now';
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays}d ago`;

  return formatShortDate(isoString);
}

export function formatShortDate(isoString?: string | null): string {
  if (!isoString) return '—';
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return '—';

  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function formatDateTime(isoString?: string | null): string {
  if (!isoString) return '—';
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return '—';

  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Formats a duration in whole seconds into a concise human-readable string (e.g. `2m 15s`, `45s`, `0s`).
 */
export function formatDurationSeconds(seconds?: number | null): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) {
    return '0s';
  }
  const total = Math.floor(seconds);
  if (total === 0) return '0s';

  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m ${secs}s`;
  }
  if (minutes > 0) {
    return secs > 0 ? `${minutes}m ${secs}s` : `${minutes}m`;
  }
  return `${secs}s`;
}

/**
 * Formats a Date into an ISO-8601 string with an explicit UTC timezone offset (`Z`),
 * matching the backend `ExplicitOffsetDateTimeField` contract.
 */
export function toExplicitOffsetIsoString(date: Date): string {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    throw new Error('Invalid date value.');
  }
  return date.toISOString();
}

export function getInitials(fullName?: string, username?: string): string {
  const source = (fullName || username || 'IQ').trim();
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
  }
  return source.slice(0, 2).toUpperCase();
}
