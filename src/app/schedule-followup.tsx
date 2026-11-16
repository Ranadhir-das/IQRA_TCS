import React, { useRef, useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { AnimatedBackButton } from '../components/AnimatedBackButton.tsx';
import { DateTimePickerModal } from '../components/DateTimePickerModal.tsx';
import { ErrorBanner } from '../components/ErrorBanner.tsx';
import { KeyboardAwareContainer } from '../components/KeyboardAwareContainer.tsx';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../context/AppThemeContext.tsx';
import { ApiError } from '../services/api.ts';
import {
  MAX_FOLLOWUP_NOTES_LENGTH,
  MAX_FOLLOWUP_REASON_LENGTH,
  createFollowUp,
  rescheduleFollowUp,
  validateFutureFollowUpSchedule,
} from '../services/followUps.ts';
import { formatDateTime, toExplicitOffsetIsoString } from '../utils/format.ts';
import { IdempotentOperationCoordinator } from '../utils/idempotency.ts';

function getDefaultScheduledAtIso(): string {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setMinutes(Math.ceil(date.getMinutes() / 15) * 15, 0, 0);
  return toExplicitOffsetIsoString(date);
}

export default function ScheduleFollowUpScreen() {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();
  const params = useLocalSearchParams<{
    mode?: 'create' | 'reschedule';
    leadId?: string;
    leadName?: string;
    callId?: string;
    followUpId?: string;
    initialScheduledAt?: string;
    initialNotes?: string;
  }>();

  const isReschedule = params.mode === 'reschedule' && Boolean(params.followUpId);
  const followUpId = Number(params.followUpId ?? 0);
  const leadId = Number(params.leadId ?? 0);
  const callId = params.callId ? Number(params.callId) : null;
  const leadName = params.leadName || (leadId ? `Lead #${leadId}` : 'Follow-Up');

  const [scheduledAtIso, setScheduledAtIso] = useState<string>(() => {
    if (params.initialScheduledAt) {
      const parsed = new Date(params.initialScheduledAt);
      if (!Number.isNaN(parsed.getTime()) && parsed.getTime() > Date.now()) {
        return toExplicitOffsetIsoString(parsed);
      }
    }
    return getDefaultScheduledAtIso();
  });
  const [notes, setNotes] = useState<string>(params.initialNotes ?? '');
  const [reason, setReason] = useState<string>('');
  const [pickerVisible, setPickerVisible] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isConflict, setIsConflict] = useState(false);

  const coordinatorRef = useRef(new IdempotentOperationCoordinator());

  const handleSave = async (forceNewKey = false) => {
    if (submitting || coordinatorRef.current.isSubmitting()) {
      return;
    }

    setSubmitError(null);
    setIsConflict(false);

    try {
      validateFutureFollowUpSchedule(scheduledAtIso);
    } catch (validationErr) {
      setSubmitError(
        validationErr instanceof Error
          ? validationErr.message
          : 'Please select a valid future date and time.'
      );
      return;
    }

    const trimmedNotes = notes.trim();
    if (trimmedNotes.length > MAX_FOLLOWUP_NOTES_LENGTH) {
      setSubmitError(
        `Notes cannot exceed ${MAX_FOLLOWUP_NOTES_LENGTH} characters.`
      );
      return;
    }

    const trimmedReason = reason.trim();
    if (trimmedReason.length > MAX_FOLLOWUP_REASON_LENGTH) {
      setSubmitError(
        `Reason cannot exceed ${MAX_FOLLOWUP_REASON_LENGTH} characters.`
      );
      return;
    }

    if (forceNewKey) {
      coordinatorRef.current.reset();
    }

    const signature = JSON.stringify({
      mode: isReschedule ? 'reschedule' : 'create',
      followUpId,
      leadId,
      callId,
      scheduledAtIso,
      notes: trimmedNotes,
      reason: trimmedReason,
    });

    setSubmitting(true);
    try {
      await coordinatorRef.current.execute(signature, async (idempotencyKey) => {
        if (isReschedule) {
          return rescheduleFollowUp(
            followUpId,
            {
              scheduled_at: scheduledAtIso,
              notes: trimmedNotes,
              reason: trimmedReason,
            },
            idempotencyKey
          );
        }
        return createFollowUp(
          {
            lead_id: leadId,
            call_id: callId && callId > 0 ? callId : null,
            scheduled_at: scheduledAtIso,
            notes: trimmedNotes,
          },
          idempotencyKey
        );
      });

      Alert.alert(
        isReschedule ? 'Follow-Up Rescheduled' : 'Follow-Up Scheduled',
        `Scheduled for ${formatDateTime(scheduledAtIso)}.`,
        [
          {
            text: 'OK',
            onPress: () => {
              if (isReschedule) {
                router.replace('/(tabs)/follow-ups');
              } else if (leadId > 0) {
                router.replace({
                  pathname: '/lead-details',
                  params: { id: String(leadId) },
                });
              } else {
                router.replace('/(tabs)/follow-ups');
              }
            },
          },
        ]
      );
    } catch (err) {
      if (err instanceof ApiError && err.isConflict) {
        setIsConflict(true);
        setSubmitError(err.message);
      } else if (err instanceof Error) {
        setSubmitError(err.message);
      } else {
        setSubmitError('Unable to save follow-up. Tap Retry to try again.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const fallbackHref = isReschedule
    ? '/(tabs)/follow-ups'
    : leadId > 0
      ? `/lead-details?id=${leadId}`
      : '/(tabs)/follow-ups';

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.headerBar}>
        <AnimatedBackButton fallbackHref={fallbackHref} />
        <View style={styles.headerCenter}>
          <Text style={styles.headerTitle}>
            {isReschedule ? 'Reschedule Follow-Up' : 'Schedule Follow-Up'}
          </Text>
          <Text style={styles.headerSubtitle} numberOfLines={1}>
            {leadName}
            {callId ? ` · Linked to Call #${callId}` : ''}
          </Text>
        </View>
        <View style={styles.headerSpacer} />
      </View>

      <KeyboardAwareContainer contentContainerStyle={styles.scrollContent}>
        {submitError ? (
          <View style={styles.bannerWrap}>
            <ErrorBanner
              message={submitError}
              actionLabel={isConflict ? 'Submit with New Key' : 'Retry'}
              onAction={() => void handleSave(isConflict)}
            />
          </View>
        ) : null}

        {/* Date & Time Selection Card */}
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Scheduled Date & Time *</Text>
          <Text style={styles.sectionHint}>
            Select a future date and time for this follow-up call.
          </Text>

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Change follow-up date and time"
            disabled={submitting}
            onPress={() => setPickerVisible(true)}
            style={({ pressed }) => [
              styles.scheduleBox,
              pressed && styles.buttonPressed,
            ]}
          >
            <View style={{ flex: 1 }}>
              <Text style={styles.scheduleLabel}>FOLLOW-UP DATE & TIME</Text>
              <Text style={styles.scheduleValue}>
                {formatDateTime(scheduledAtIso)}
              </Text>
            </View>
            <View style={styles.changePill}>
              <Text style={styles.changePillText}>Change</Text>
            </View>
          </Pressable>
        </View>

        {/* Reschedule Reason (Reschedule Mode Only) */}
        {isReschedule ? (
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Reason for Rescheduling (Optional)</Text>
            <TextInput
              value={reason}
              onChangeText={(val) => {
                setReason(val);
                setSubmitError(null);
              }}
              placeholder="e.g. Student asked to call back tomorrow afternoon…"
              placeholderTextColor={colors.placeholder}
              maxLength={MAX_FOLLOWUP_REASON_LENGTH}
              editable={!submitting}
              style={styles.reasonInput}
            />
          </View>
        ) : null}

        {/* Follow-Up Notes */}
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Follow-Up Notes (Optional)</Text>
          <TextInput
            value={notes}
            onChangeText={(val) => {
              setNotes(val);
              setSubmitError(null);
            }}
            placeholder="Add context or talking points for this follow-up…"
            placeholderTextColor={colors.placeholder}
            multiline
            numberOfLines={4}
            maxLength={MAX_FOLLOWUP_NOTES_LENGTH}
            editable={!submitting}
            textAlignVertical="top"
            style={styles.notesInput}
          />
          <Text style={styles.charCountText}>
            {notes.trim().length} / {MAX_FOLLOWUP_NOTES_LENGTH}
          </Text>
        </View>

        <Pressable
          accessibilityRole="button"
          disabled={submitting}
          onPress={() => void handleSave(false)}
          style={({ pressed }) => [
            styles.primaryButton,
            submitting && styles.buttonDisabled,
            pressed && !submitting && styles.buttonPressed,
          ]}
        >
          {submitting ? (
            <View style={styles.submittingRow}>
              <ActivityIndicator size="small" color={colors.onPrimary} />
              <Text style={styles.primaryButtonText}>Saving Follow-Up…</Text>
            </View>
          ) : (
            <Text style={styles.primaryButtonText}>
              {submitError
                ? 'Retry Saving Follow-Up'
                : isReschedule
                  ? 'Confirm New Schedule'
                  : 'Save Follow-Up'}
            </Text>
          )}
        </Pressable>
      </KeyboardAwareContainer>

      <DateTimePickerModal
        visible={pickerVisible}
        initialIso={scheduledAtIso}
        title={
          isReschedule
            ? 'Select New Follow-Up Time'
            : 'Select Follow-Up Date & Time'
        }
        onClose={() => setPickerVisible(false)}
        onConfirm={(nextIso) => {
          setScheduledAtIso(nextIso);
          setPickerVisible(false);
          setSubmitError(null);
        }}
      />
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
    headerCenter: {
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
      marginTop: 2,
    },
    headerSpacer: {
      width: 40,
    },
    scrollContent: {
      paddingHorizontal: 18,
      paddingVertical: 16,
      gap: 16,
    },
    bannerWrap: {
      marginBottom: 2,
    },
    card: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      padding: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    sectionTitle: {
      fontSize: 15.5,
      fontWeight: '700',
      color: colors.text,
    },
    sectionHint: {
      fontSize: 12.5,
      lineHeight: 18,
      color: colors.muted,
      marginTop: 4,
      marginBottom: 12,
    },
    scheduleBox: {
      backgroundColor: colors.background,
      borderWidth: 1,
      borderColor: colors.primary,
      borderRadius: 12,
      paddingHorizontal: 14,
      paddingVertical: 13,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
    },
    scheduleLabel: {
      fontSize: 11,
      fontWeight: '700',
      color: colors.muted,
      letterSpacing: 0.5,
    },
    scheduleValue: {
      fontSize: 15.5,
      fontWeight: '800',
      color: colors.text,
      marginTop: 3,
    },
    changePill: {
      backgroundColor: colors.accentSoft,
      paddingHorizontal: 12,
      paddingVertical: 6,
      borderRadius: 999,
    },
    changePillText: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.primary,
    },
    reasonInput: {
      marginTop: 10,
      backgroundColor: colors.background,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      paddingHorizontal: 12,
      paddingVertical: 11,
      fontSize: 14.5,
      color: colors.text,
    },
    notesInput: {
      marginTop: 10,
      backgroundColor: colors.background,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 12,
      paddingHorizontal: 12,
      paddingVertical: 11,
      minHeight: 96,
      fontSize: 14.5,
      color: colors.text,
    },
    charCountText: {
      fontSize: 11.5,
      color: colors.muted,
      textAlign: 'right',
      marginTop: 6,
    },
    primaryButton: {
      backgroundColor: colors.primary,
      borderRadius: 13,
      paddingVertical: 14,
      paddingHorizontal: 16,
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: 48,
    },
    primaryButtonText: {
      color: colors.onPrimary,
      fontSize: 15,
      fontWeight: '700',
    },
    submittingRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
    },
    buttonDisabled: {
      opacity: 0.6,
    },
    buttonPressed: {
      opacity: 0.84,
    },
  });
