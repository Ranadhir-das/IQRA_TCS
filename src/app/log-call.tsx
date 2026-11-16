import React, { useCallback, useEffect, useRef, useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { AnimatedBackButton } from '../components/AnimatedBackButton.tsx';
import { ErrorBanner } from '../components/ErrorBanner.tsx';
import { KeyboardAwareContainer } from '../components/KeyboardAwareContainer.tsx';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../context/AppThemeContext.tsx';
import { ApiError } from '../services/api.ts';
import {
  MAX_CALL_DURATION_SECONDS,
  MAX_EMPLOYEE_NOTES_LENGTH,
  computeCallTimestampsFromDuration,
  fetchActiveCallOutcomes,
  submitCallOutcome,
} from '../services/calls.ts';
import type { CallOutcome, CallSubmissionResponse } from '../types/index.ts';
import { formatDateTime, formatDurationSeconds } from '../utils/format.ts';
import { IdempotentOperationCoordinator } from '../utils/idempotency.ts';

const DURATION_PRESETS: { label: string; seconds: number }[] = [
  { label: '0s (Not Connected)', seconds: 0 },
  { label: '30s', seconds: 30 },
  { label: '1m', seconds: 60 },
  { label: '2m', seconds: 120 },
  { label: '3m', seconds: 180 },
  { label: '5m', seconds: 300 },
  { label: '10m', seconds: 600 },
];

export default function LogCallScreen() {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();
  const params = useLocalSearchParams<{
    leadId?: string;
    leadName?: string;
    leadPhone?: string;
    dialerOpenedAt?: string;
  }>();

  const leadId = Number(params.leadId ?? 0);
  const leadName = params.leadName || (leadId ? `Lead #${leadId}` : 'Lead');
  const leadPhone = params.leadPhone || '';

  const [outcomes, setOutcomes] = useState<CallOutcome[]>([]);
  const [loadingOutcomes, setLoadingOutcomes] = useState(true);
  const [outcomesError, setOutcomesError] = useState<string | null>(null);

  const [selectedOutcomeCode, setSelectedOutcomeCode] = useState<string>('');
  const [durationMinutes, setDurationMinutes] = useState<string>('1');
  const [durationSecondsPart, setDurationSecondsPart] = useState<string>('0');
  const [employeeNotes, setEmployeeNotes] = useState<string>('');

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isConflictError, setIsConflictError] = useState(false);
  const [submittedCall, setSubmittedCall] = useState<{
    call: CallSubmissionResponse;
    created: boolean;
  } | null>(null);

  // Coordinator preserves the exact UUID `client_event_id` and frozen `(started_at, ended_at)`
  // across retries of the same logical submission.
  const coordinatorRef = useRef(
    new IdempotentOperationCoordinator<{
      started_at: string;
      ended_at: string;
    }>()
  );

  const loadOutcomes = useCallback(async () => {
    setLoadingOutcomes(true);
    setOutcomesError(null);
    try {
      const list = await fetchActiveCallOutcomes();
      setOutcomes(list);
    } catch (err) {
      setOutcomesError(
        err instanceof Error
          ? err.message
          : 'Could not load call outcomes. Please retry.'
      );
    } finally {
      setLoadingOutcomes(false);
    }
  }, []);

  useEffect(() => {
    void loadOutcomes();
  }, [loadOutcomes]);

  const parsedMinutes = Number(durationMinutes || '0');
  const parsedSeconds = Number(durationSecondsPart || '0');
  const totalDurationSeconds =
    Number.isInteger(parsedMinutes) &&
    parsedMinutes >= 0 &&
    Number.isInteger(parsedSeconds) &&
    parsedSeconds >= 0 &&
    parsedSeconds < 60
      ? parsedMinutes * 60 + parsedSeconds
      : -1;

  const applyDurationPreset = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    setDurationMinutes(String(mins));
    setDurationSecondsPart(String(secs));
    setSubmitError(null);
  };

  const handleSubmit = async (forceNewKey = false) => {
    if (submitting || coordinatorRef.current.isSubmitting()) {
      return;
    }

    setSubmitError(null);
    setIsConflictError(false);

    if (!Number.isInteger(leadId) || leadId <= 0) {
      setSubmitError('Invalid lead ID. Please return to the lead screen and try again.');
      return;
    }

    if (!selectedOutcomeCode) {
      setSubmitError('Please select a call outcome before submitting.');
      return;
    }

    if (
      totalDurationSeconds < 0 ||
      totalDurationSeconds > MAX_CALL_DURATION_SECONDS
    ) {
      setSubmitError(
        'Please enter a valid call duration (0–59 seconds and up to 24 hours total).'
      );
      return;
    }

    const trimmedNotes = employeeNotes.trim();
    if (trimmedNotes.length > MAX_EMPLOYEE_NOTES_LENGTH) {
      setSubmitError(
        `Notes cannot exceed ${MAX_EMPLOYEE_NOTES_LENGTH} characters.`
      );
      return;
    }

    if (forceNewKey) {
      coordinatorRef.current.reset();
    }

    const logicalSignature = JSON.stringify({
      leadId,
      outcomeCode: selectedOutcomeCode,
      durationSeconds: totalDurationSeconds,
      notes: trimmedNotes,
    });

    setSubmitting(true);
    try {
      const result = await coordinatorRef.current.execute(
        logicalSignature,
        async (clientEventId, timeWindow) => {
          return submitCallOutcome({
            lead_id: leadId,
            client_event_id: clientEventId,
            outcome_code: selectedOutcomeCode,
            started_at: timeWindow.started_at,
            ended_at: timeWindow.ended_at,
            employee_notes: trimmedNotes,
          });
        },
        () => computeCallTimestampsFromDuration(totalDurationSeconds)
      );

      setSubmittedCall({
        call: result.call,
        created: result.created,
      });
    } catch (err) {
      if (err instanceof ApiError && err.isConflict) {
        setIsConflictError(true);
        setSubmitError(
          err.message ||
            'This call submission conflicted with an earlier event ID. Tap "Submit as New Event" to log a fresh entry.'
        );
      } else if (err instanceof Error) {
        setSubmitError(err.message);
      } else {
        setSubmitError('Could not submit call outcome. Your entry is preserved — tap Retry.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (submittedCall) {
    const { call, created } = submittedCall;
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.headerBar}>
          <AnimatedBackButton fallbackHref={`/lead-details?id=${leadId}`} />
          <Text style={styles.headerTitle}>Call Logged</Text>
          <View style={styles.headerSpacer} />
        </View>

        <View style={styles.confirmationContainer}>
          <View style={styles.confirmationCard}>
            <View style={styles.successCircle}>
              <Text style={styles.successCircleText}>✓</Text>
            </View>
            <Text style={styles.confirmationTitle}>
              {created ? 'Call Outcome Saved' : 'Call Outcome Confirmed'}
            </Text>
            <Text style={styles.confirmationSubtitle}>
              {created
                ? `Your call for ${leadName} has been recorded.`
                : `Your existing call record for ${leadName} was confirmed without creating a duplicate.`}
            </Text>

            <View style={styles.summaryBox}>
              <View style={styles.summaryRow}>
                <Text style={styles.summaryLabel}>Lead</Text>
                <Text style={styles.summaryValue}>{leadName}</Text>
              </View>
              <View style={styles.summaryRow}>
                <Text style={styles.summaryLabel}>Outcome</Text>
                <Text style={styles.summaryValue}>
                  {call.outcome_label_snapshot || call.outcome_code_snapshot}
                </Text>
              </View>
              <View style={styles.summaryRow}>
                <Text style={styles.summaryLabel}>Duration</Text>
                <Text style={styles.summaryValue}>
                  {formatDurationSeconds(call.duration_seconds)}
                </Text>
              </View>
              <View style={styles.summaryRow}>
                <Text style={styles.summaryLabel}>Recorded At</Text>
                <Text style={styles.summaryValue}>
                  {formatDateTime(call.ended_at)}
                </Text>
              </View>
            </View>

            <Pressable
              accessibilityRole="button"
              onPress={() =>
                router.replace({
                  pathname: '/schedule-followup',
                  params: {
                    mode: 'create',
                    leadId: String(leadId),
                    leadName,
                    callId: String(call.id),
                  },
                })
              }
              style={({ pressed }) => [
                styles.primaryButton,
                pressed && styles.buttonPressed,
              ]}
            >
              <Text style={styles.primaryButtonText}>
                Schedule Follow-Up for This Call
              </Text>
            </Pressable>

            <Pressable
              accessibilityRole="button"
              onPress={() =>
                router.replace({
                  pathname: '/lead-details',
                  params: { id: String(leadId) },
                })
              }
              style={({ pressed }) => [
                styles.secondaryButton,
                pressed && styles.buttonPressed,
              ]}
            >
              <Text style={styles.secondaryButtonText}>Done — Back to Lead</Text>
            </Pressable>
          </View>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.headerBar}>
        <AnimatedBackButton fallbackHref={`/lead-details?id=${leadId}`} />
        <View style={styles.headerCenter}>
          <Text style={styles.headerTitle} numberOfLines={1}>
            Log Call Outcome
          </Text>
          <Text style={styles.headerSubtitle} numberOfLines={1}>
            {leadName}
            {leadPhone ? ` · ${leadPhone}` : ''}
          </Text>
        </View>
        <View style={styles.headerSpacer} />
      </View>

      <KeyboardAwareContainer contentContainerStyle={styles.scrollContent}>
        {params.dialerOpenedAt ? (
          <View style={styles.bannerWrap}>
            <ErrorBanner
              variant="info"
              message={`Dialer opened at ${formatDateTime(params.dialerOpenedAt)}. Select the call outcome and confirm the call duration below.`}
            />
          </View>
        ) : null}

        {submitError ? (
          <View style={styles.bannerWrap}>
            <ErrorBanner
              variant="error"
              message={submitError}
              actionLabel={isConflictError ? 'Submit as New Event' : 'Retry'}
              onAction={() => void handleSubmit(isConflictError)}
            />
          </View>
        ) : null}

        {/* Step 1: Select Dynamic Call Outcome */}
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>1. Select Call Outcome *</Text>
          <Text style={styles.sectionHint}>
            Outcomes are loaded dynamically from the IQRA TCS server.
          </Text>

          {loadingOutcomes ? (
            <View style={styles.outcomesLoadingBox}>
              <ActivityIndicator size="small" color={colors.primary} />
              <Text style={styles.outcomesLoadingText}>
                Loading active outcomes…
              </Text>
            </View>
          ) : outcomesError ? (
            <ErrorBanner
              message={outcomesError}
              actionLabel="Retry"
              onAction={() => void loadOutcomes()}
            />
          ) : outcomes.length === 0 ? (
            <Text style={styles.emptyOutcomesText}>
              No active call outcomes are currently configured. Please contact your
              administrator.
            </Text>
          ) : (
            <View style={styles.outcomesGrid}>
              {outcomes.map((outcome) => {
                const selected = selectedOutcomeCode === outcome.code;
                return (
                  <Pressable
                    key={outcome.id}
                    accessibilityRole="button"
                    accessibilityState={{ selected }}
                    disabled={submitting}
                    onPress={() => {
                      setSelectedOutcomeCode(outcome.code);
                      setSubmitError(null);
                    }}
                    style={[
                      styles.outcomeOption,
                      selected && styles.outcomeOptionSelected,
                    ]}
                  >
                    <Text
                      style={[
                        styles.outcomeLabel,
                        selected && styles.outcomeLabelSelected,
                      ]}
                    >
                      {outcome.label}
                    </Text>
                    <Text
                      style={[
                        styles.outcomeCode,
                        selected && styles.outcomeCodeSelected,
                      ]}
                    >
                      {outcome.code}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          )}
        </View>

        {/* Step 2: Employee-Confirmed Call Duration */}
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>
            2. Confirm Call Duration *
          </Text>
          <Text style={styles.sectionHint}>
            Enter or select the actual connected call duration (use 0s if the call was
            not answered, busy, or unreachable).
          </Text>

          <View style={styles.presetsRow}>
            {DURATION_PRESETS.map((preset) => {
              const selected = totalDurationSeconds === preset.seconds;
              return (
                <Pressable
                  key={preset.label}
                  accessibilityRole="button"
                  disabled={submitting}
                  onPress={() => applyDurationPreset(preset.seconds)}
                  style={[
                    styles.presetPill,
                    selected && styles.presetPillSelected,
                  ]}
                >
                  <Text
                    style={[
                      styles.presetPillText,
                      selected && styles.presetPillTextSelected,
                    ]}
                  >
                    {preset.label}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          <View style={styles.durationInputsRow}>
            <View style={styles.durationFieldCol}>
              <Text style={styles.fieldLabel}>Minutes</Text>
              <TextInput
                value={durationMinutes}
                onChangeText={(val) => {
                  setDurationMinutes(val.replace(/\D/g, '').slice(0, 4));
                  setSubmitError(null);
                }}
                keyboardType="number-pad"
                editable={!submitting}
                placeholder="0"
                placeholderTextColor={colors.placeholder}
                style={styles.durationInput}
              />
            </View>

            <View style={styles.durationFieldCol}>
              <Text style={styles.fieldLabel}>Seconds (0–59)</Text>
              <TextInput
                value={durationSecondsPart}
                onChangeText={(val) => {
                  setDurationSecondsPart(val.replace(/\D/g, '').slice(0, 2));
                  setSubmitError(null);
                }}
                keyboardType="number-pad"
                editable={!submitting}
                placeholder="0"
                placeholderTextColor={colors.placeholder}
                style={styles.durationInput}
              />
            </View>

            <View style={styles.durationSummaryBox}>
              <Text style={styles.durationSummaryLabel}>Total</Text>
              <Text style={styles.durationSummaryValue}>
                {totalDurationSeconds >= 0
                  ? formatDurationSeconds(totalDurationSeconds)
                  : 'Invalid'}
              </Text>
            </View>
          </View>
        </View>

        {/* Step 3: Optional Employee Notes */}
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>3. Call Notes (Optional)</Text>
          <TextInput
            value={employeeNotes}
            onChangeText={(val) => {
              setEmployeeNotes(val);
              setSubmitError(null);
            }}
            placeholder="Add any relevant conversation notes or student requirements…"
            placeholderTextColor={colors.placeholder}
            multiline
            numberOfLines={4}
            maxLength={MAX_EMPLOYEE_NOTES_LENGTH}
            editable={!submitting}
            textAlignVertical="top"
            style={styles.notesInput}
          />
          <Text style={styles.charCountText}>
            {employeeNotes.trim().length} / {MAX_EMPLOYEE_NOTES_LENGTH}
          </Text>
        </View>

        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Submit Call Outcome"
          disabled={submitting || loadingOutcomes || outcomes.length === 0}
          onPress={() => void handleSubmit(false)}
          style={({ pressed }) => [
            styles.primaryButton,
            (submitting || loadingOutcomes || outcomes.length === 0) &&
              styles.buttonDisabled,
            pressed && !submitting && styles.buttonPressed,
          ]}
        >
          {submitting ? (
            <View style={styles.submittingRow}>
              <ActivityIndicator size="small" color={colors.onPrimary} />
              <Text style={styles.primaryButtonText}>Saving Call Log…</Text>
            </View>
          ) : (
            <Text style={styles.primaryButtonText}>
              {submitError ? 'Retry Call Submission' : 'Save Call Outcome'}
            </Text>
          )}
        </Pressable>
      </KeyboardAwareContainer>
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
    outcomesLoadingBox: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 10,
      paddingVertical: 14,
    },
    outcomesLoadingText: {
      fontSize: 13.5,
      color: colors.muted,
    },
    emptyOutcomesText: {
      fontSize: 13.5,
      color: colors.danger,
      paddingVertical: 8,
    },
    outcomesGrid: {
      gap: 8,
    },
    outcomeOption: {
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.background,
      borderRadius: 12,
      paddingHorizontal: 14,
      paddingVertical: 12,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 10,
    },
    outcomeOptionSelected: {
      borderColor: colors.primary,
      backgroundColor: colors.accentSoft,
    },
    outcomeLabel: {
      flex: 1,
      fontSize: 14.5,
      fontWeight: '700',
      color: colors.text,
    },
    outcomeLabelSelected: {
      color: colors.primary,
    },
    outcomeCode: {
      fontSize: 11,
      fontWeight: '700',
      color: colors.muted,
    },
    outcomeCodeSelected: {
      color: colors.primary,
    },
    presetsRow: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 8,
      marginBottom: 14,
    },
    presetPill: {
      paddingHorizontal: 11,
      paddingVertical: 7,
      borderRadius: 999,
      backgroundColor: colors.background,
      borderWidth: 1,
      borderColor: colors.border,
    },
    presetPillSelected: {
      backgroundColor: colors.primary,
      borderColor: colors.primary,
    },
    presetPillText: {
      fontSize: 12.5,
      fontWeight: '600',
      color: colors.secondary,
    },
    presetPillTextSelected: {
      color: colors.onPrimary,
      fontWeight: '700',
    },
    durationInputsRow: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      gap: 10,
    },
    durationFieldCol: {
      flex: 1,
    },
    fieldLabel: {
      fontSize: 12.5,
      fontWeight: '600',
      color: colors.secondary,
      marginBottom: 6,
    },
    durationInput: {
      backgroundColor: colors.background,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 10,
      paddingHorizontal: 12,
      paddingVertical: 10,
      fontSize: 15,
      fontWeight: '700',
      color: colors.text,
      textAlign: 'center',
    },
    durationSummaryBox: {
      flex: 1,
      backgroundColor: colors.surfaceMuted,
      borderRadius: 10,
      paddingVertical: 8,
      paddingHorizontal: 10,
      alignItems: 'center',
      justifyContent: 'center',
    },
    durationSummaryLabel: {
      fontSize: 11,
      fontWeight: '600',
      color: colors.muted,
    },
    durationSummaryValue: {
      fontSize: 14,
      fontWeight: '800',
      color: colors.text,
      marginTop: 2,
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
    secondaryButton: {
      marginTop: 10,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 13,
      paddingVertical: 13,
      paddingHorizontal: 16,
      alignItems: 'center',
      justifyContent: 'center',
    },
    secondaryButtonText: {
      color: colors.text,
      fontSize: 14.5,
      fontWeight: '600',
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
    confirmationContainer: {
      flex: 1,
      justifyContent: 'center',
      paddingHorizontal: 20,
    },
    confirmationCard: {
      backgroundColor: colors.surface,
      borderRadius: 18,
      padding: 22,
      borderWidth: 1,
      borderColor: colors.border,
    },
    successCircle: {
      width: 54,
      height: 54,
      borderRadius: 27,
      backgroundColor: colors.successSoft,
      alignSelf: 'center',
      alignItems: 'center',
      justifyContent: 'center',
      marginBottom: 14,
    },
    successCircleText: {
      fontSize: 24,
      fontWeight: '800',
      color: colors.success,
    },
    confirmationTitle: {
      fontSize: 20,
      fontWeight: '800',
      color: colors.text,
      textAlign: 'center',
    },
    confirmationSubtitle: {
      fontSize: 13.5,
      lineHeight: 19,
      color: colors.muted,
      textAlign: 'center',
      marginTop: 6,
      marginBottom: 18,
    },
    summaryBox: {
      backgroundColor: colors.surfaceMuted,
      borderRadius: 12,
      paddingHorizontal: 14,
      paddingVertical: 10,
      marginBottom: 18,
      gap: 8,
    },
    summaryRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
    },
    summaryLabel: {
      fontSize: 13,
      fontWeight: '600',
      color: colors.muted,
    },
    summaryValue: {
      fontSize: 13.5,
      fontWeight: '700',
      color: colors.text,
      textAlign: 'right',
      flexShrink: 1,
    },
  });
