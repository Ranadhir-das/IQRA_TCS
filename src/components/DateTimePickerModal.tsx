import React, { useEffect, useMemo, useState } from 'react';
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import type { AppColors } from '../constants/theme.ts';
import { useAppStyles, useAppTheme } from '../context/AppThemeContext.tsx';
import { formatDateTime, toExplicitOffsetIsoString } from '../utils/format.ts';

export interface DateTimePickerModalProps {
  visible: boolean;
  initialIso?: string | null;
  title?: string;
  onClose: () => void;
  onConfirm: (isoTimestamp: string) => void;
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

function toLocalYmd(date: Date): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function defaultFutureDate(): Date {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setMinutes(Math.ceil(date.getMinutes() / 15) * 15, 0, 0);
  return date;
}

export function DateTimePickerModal({
  visible,
  initialIso,
  title = 'Select Follow-Up Date & Time',
  onClose,
  onConfirm,
}: DateTimePickerModalProps) {
  const styles = useAppStyles(createStyles);
  const { colors } = useAppTheme();

  const [dateYmd, setDateYmd] = useState('');
  const [hour12, setHour12] = useState(10);
  const [minute, setMinute] = useState('00');
  const [meridiem, setMeridiem] = useState<'AM' | 'PM'>('AM');
  const [validationError, setValidationError] = useState<string | null>(null);

  const applyDateObject = (target: Date) => {
    setDateYmd(toLocalYmd(target));
    const h24 = target.getHours();
    const isPm = h24 >= 12;
    const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
    setHour12(h12);
    setMinute(pad2(target.getMinutes()));
    setMeridiem(isPm ? 'PM' : 'AM');
    setValidationError(null);
  };

  useEffect(() => {
    if (!visible) return;
    if (initialIso) {
      const parsed = new Date(initialIso);
      if (!Number.isNaN(parsed.getTime()) && parsed.getTime() > Date.now()) {
        applyDateObject(parsed);
        return;
      }
    }
    applyDateObject(defaultFutureDate());
  }, [visible, initialIso]);

  const upcomingDays = useMemo(() => {
    const base = new Date();
    base.setHours(0, 0, 0, 0);
    return Array.from({ length: 14 }, (_, idx) => {
      const d = new Date(base);
      d.setDate(base.getDate() + idx);
      const ymd = toLocalYmd(d);
      const label =
        idx === 0
          ? 'Today'
          : idx === 1
            ? 'Tomorrow'
            : d.toLocaleDateString(undefined, {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
              });
      return { ymd, label };
    });
  }, []);

  const buildSelectedDate = (): Date | null => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateYmd.trim());
    if (!match) return null;
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const parsedMinute = Number(minute);
    if (
      month < 1 ||
      month > 12 ||
      day < 1 ||
      day > 31 ||
      !Number.isInteger(parsedMinute) ||
      parsedMinute < 0 ||
      parsedMinute > 59
    ) {
      return null;
    }

    let h24 = hour12 % 12;
    if (meridiem === 'PM') {
      h24 += 12;
    }

    const candidate = new Date(year, month - 1, day, h24, parsedMinute, 0, 0);
    if (
      Number.isNaN(candidate.getTime()) ||
      candidate.getFullYear() !== year ||
      candidate.getMonth() !== month - 1 ||
      candidate.getDate() !== day
    ) {
      return null;
    }
    return candidate;
  };

  const selectedDate = buildSelectedDate();

  const applyQuickPreset = (preset: 'in30m' | 'in2h' | 'tomorrow10' | 'tomorrow15' | 'in2days10' | 'nextWeek10') => {
    const now = new Date();
    const target = new Date(now);
    if (preset === 'in30m') {
      target.setMinutes(now.getMinutes() + 30, 0, 0);
    } else if (preset === 'in2h') {
      target.setHours(now.getHours() + 2, now.getMinutes(), 0, 0);
    } else if (preset === 'tomorrow10') {
      target.setDate(now.getDate() + 1);
      target.setHours(10, 0, 0, 0);
    } else if (preset === 'tomorrow15') {
      target.setDate(now.getDate() + 1);
      target.setHours(15, 0, 0, 0);
    } else if (preset === 'in2days10') {
      target.setDate(now.getDate() + 2);
      target.setHours(10, 0, 0, 0);
    } else {
      target.setDate(now.getDate() + 7);
      target.setHours(10, 0, 0, 0);
    }
    applyDateObject(target);
  };

  const handleConfirm = () => {
    const candidate = buildSelectedDate();
    if (!candidate) {
      setValidationError('Please enter a valid date (YYYY-MM-DD) and minute (00–59).');
      return;
    }
    if (candidate.getTime() <= Date.now()) {
      setValidationError('Follow-up date and time must be in the future.');
      return;
    }
    setValidationError(null);
    onConfirm(toExplicitOffsetIsoString(candidate));
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
    >
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.headerRow}>
            <Text style={styles.title}>{title}</Text>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close picker"
              onPress={onClose}
              style={styles.closeBtn}
            >
              <Text style={styles.closeBtnText}>✕</Text>
            </Pressable>
          </View>

          <ScrollView
            showsVerticalScrollIndicator={false}
            contentContainerStyle={styles.sheetContent}
          >
            {/* Quick Presets */}
            <Text style={styles.sectionLabel}>Quick Schedule Presets</Text>
            <View style={styles.presetsWrap}>
              {[
                { id: 'in30m', label: 'In 30 mins' },
                { id: 'in2h', label: 'In 2 hours' },
                { id: 'tomorrow10', label: 'Tomorrow 10:00 AM' },
                { id: 'tomorrow15', label: 'Tomorrow 3:00 PM' },
                { id: 'in2days10', label: 'In 2 Days 10:00 AM' },
                { id: 'nextWeek10', label: 'Next Week 10:00 AM' },
              ].map((preset) => (
                <Pressable
                  key={preset.id}
                  accessibilityRole="button"
                  onPress={() =>
                    applyQuickPreset(
                      preset.id as
                        | 'in30m'
                        | 'in2h'
                        | 'tomorrow10'
                        | 'tomorrow15'
                        | 'in2days10'
                        | 'nextWeek10'
                    )
                  }
                  style={({ pressed }) => [
                    styles.presetChip,
                    pressed && styles.chipPressed,
                  ]}
                >
                  <Text style={styles.presetChipText}>{preset.label}</Text>
                </Pressable>
              ))}
            </View>

            {/* Date Selection */}
            <Text style={styles.sectionLabel}>Date</Text>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.dateRow}
            >
              {upcomingDays.map((day) => {
                const selected = dateYmd === day.ymd;
                return (
                  <Pressable
                    key={day.ymd}
                    accessibilityRole="button"
                    accessibilityState={{ selected }}
                    onPress={() => {
                      setDateYmd(day.ymd);
                      setValidationError(null);
                    }}
                    style={[
                      styles.dateChip,
                      selected && styles.dateChipSelected,
                    ]}
                  >
                    <Text
                      style={[
                        styles.dateChipText,
                        selected && styles.dateChipTextSelected,
                      ]}
                    >
                      {day.label}
                    </Text>
                    <Text
                      style={[
                        styles.dateChipSubtext,
                        selected && styles.dateChipTextSelected,
                      ]}
                    >
                      {day.ymd.slice(5)}
                    </Text>
                  </Pressable>
                );
              })}
            </ScrollView>

            <View style={styles.customDateRow}>
              <Text style={styles.customDateLabel}>Date (YYYY-MM-DD):</Text>
              <TextInput
                value={dateYmd}
                onChangeText={(val) => {
                  setDateYmd(val);
                  setValidationError(null);
                }}
                placeholder="YYYY-MM-DD"
                placeholderTextColor={colors.placeholder}
                autoCapitalize="none"
                autoCorrect={false}
                style={styles.customDateInput}
              />
            </View>

            {/* Hour Selection */}
            <Text style={styles.sectionLabel}>Hour</Text>
            <View style={styles.hoursGrid}>
              {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map((h) => {
                const selected = hour12 === h;
                return (
                  <Pressable
                    key={h}
                    accessibilityRole="button"
                    accessibilityState={{ selected }}
                    onPress={() => {
                      setHour12(h);
                      setValidationError(null);
                    }}
                    style={[
                      styles.hourChip,
                      selected && styles.dateChipSelected,
                    ]}
                  >
                    <Text
                      style={[
                        styles.hourChipText,
                        selected && styles.dateChipTextSelected,
                      ]}
                    >
                      {h}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            {/* Minute & AM/PM Selection */}
            <View style={styles.timeBottomRow}>
              <View style={styles.minuteCol}>
                <Text style={styles.sectionLabel}>Minute</Text>
                <View style={styles.minuteChipsRow}>
                  {['00', '15', '30', '45'].map((m) => {
                    const selected = minute === m;
                    return (
                      <Pressable
                        key={m}
                        accessibilityRole="button"
                        onPress={() => {
                          setMinute(m);
                          setValidationError(null);
                        }}
                        style={[
                          styles.minuteChip,
                          selected && styles.dateChipSelected,
                        ]}
                      >
                        <Text
                          style={[
                            styles.hourChipText,
                            selected && styles.dateChipTextSelected,
                          ]}
                        >
                          :{m}
                        </Text>
                      </Pressable>
                    );
                  })}
                  <TextInput
                    value={minute}
                    onChangeText={(val) => {
                      setMinute(val.replace(/\D/g, '').slice(0, 2));
                      setValidationError(null);
                    }}
                    keyboardType="number-pad"
                    maxLength={2}
                    placeholder="00"
                    placeholderTextColor={colors.placeholder}
                    style={styles.minuteInput}
                  />
                </View>
              </View>

              <View style={styles.meridiemCol}>
                <Text style={styles.sectionLabel}>AM / PM</Text>
                <View style={styles.meridiemRow}>
                  {(['AM', 'PM'] as const).map((m) => {
                    const selected = meridiem === m;
                    return (
                      <Pressable
                        key={m}
                        accessibilityRole="button"
                        onPress={() => {
                          setMeridiem(m);
                          setValidationError(null);
                        }}
                        style={[
                          styles.meridiemChip,
                          selected && styles.dateChipSelected,
                        ]}
                      >
                        <Text
                          style={[
                            styles.hourChipText,
                            selected && styles.dateChipTextSelected,
                          ]}
                        >
                          {m}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              </View>
            </View>

            {/* Live Preview */}
            <View style={styles.previewBox}>
              <Text style={styles.previewLabel}>Selected Follow-Up Schedule</Text>
              <Text style={styles.previewValue}>
                {selectedDate
                  ? formatDateTime(selectedDate.toISOString())
                  : 'Invalid date or time'}
              </Text>
            </View>

            {validationError ? (
              <Text style={styles.validationText}>{validationError}</Text>
            ) : null}
          </ScrollView>

          <View style={styles.footerRow}>
            <Pressable
              accessibilityRole="button"
              onPress={onClose}
              style={styles.cancelBtn}
            >
              <Text style={styles.cancelBtnText}>Cancel</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              onPress={handleConfirm}
              style={styles.confirmBtn}
            >
              <Text style={styles.confirmBtnText}>Confirm Schedule</Text>
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const createStyles = (colors: AppColors) =>
  StyleSheet.create({
    backdrop: {
      flex: 1,
      backgroundColor: 'rgba(8, 18, 30, 0.6)',
      justifyContent: 'flex-end',
    },
    sheet: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 22,
      borderTopRightRadius: 22,
      maxHeight: '88%',
      borderTopWidth: 1,
      borderColor: colors.border,
    },
    headerRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 18,
      paddingVertical: 14,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    title: {
      fontSize: 17,
      fontWeight: '700',
      color: colors.text,
    },
    closeBtn: {
      width: 32,
      height: 32,
      borderRadius: 16,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: colors.surfaceMuted,
    },
    closeBtnText: {
      fontSize: 14,
      fontWeight: '700',
      color: colors.secondary,
    },
    sheetContent: {
      paddingHorizontal: 18,
      paddingVertical: 14,
      gap: 12,
    },
    sectionLabel: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.muted,
      textTransform: 'uppercase',
      letterSpacing: 0.5,
    },
    presetsWrap: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 8,
    },
    presetChip: {
      backgroundColor: colors.accentSoft,
      paddingHorizontal: 12,
      paddingVertical: 8,
      borderRadius: 999,
    },
    presetChipText: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.primary,
    },
    chipPressed: {
      opacity: 0.75,
    },
    dateRow: {
      flexDirection: 'row',
      gap: 8,
      paddingVertical: 2,
    },
    dateChip: {
      paddingHorizontal: 12,
      paddingVertical: 8,
      borderRadius: 12,
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.background,
      alignItems: 'center',
      minWidth: 84,
    },
    dateChipSelected: {
      backgroundColor: colors.primary,
      borderColor: colors.primary,
    },
    dateChipText: {
      fontSize: 12.5,
      fontWeight: '700',
      color: colors.text,
    },
    dateChipSubtext: {
      fontSize: 11,
      color: colors.muted,
      marginTop: 2,
    },
    dateChipTextSelected: {
      color: colors.onPrimary,
    },
    customDateRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 10,
    },
    customDateLabel: {
      fontSize: 13,
      fontWeight: '600',
      color: colors.secondary,
    },
    customDateInput: {
      flex: 1,
      maxWidth: 150,
      backgroundColor: colors.background,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 10,
      paddingHorizontal: 12,
      paddingVertical: 8,
      fontSize: 14,
      color: colors.text,
      textAlign: 'center',
    },
    hoursGrid: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 8,
    },
    hourChip: {
      width: 46,
      height: 38,
      borderRadius: 10,
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.background,
      alignItems: 'center',
      justifyContent: 'center',
    },
    hourChipText: {
      fontSize: 13.5,
      fontWeight: '700',
      color: colors.text,
    },
    timeBottomRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      gap: 14,
    },
    minuteCol: {
      flex: 1.4,
      gap: 8,
    },
    minuteChipsRow: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 6,
      alignItems: 'center',
    },
    minuteChip: {
      paddingHorizontal: 10,
      height: 38,
      borderRadius: 10,
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.background,
      alignItems: 'center',
      justifyContent: 'center',
    },
    minuteInput: {
      width: 50,
      height: 38,
      borderRadius: 10,
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.background,
      fontSize: 14,
      fontWeight: '700',
      color: colors.text,
      textAlign: 'center',
    },
    meridiemCol: {
      flex: 0.9,
      gap: 8,
    },
    meridiemRow: {
      flexDirection: 'row',
      gap: 6,
    },
    meridiemChip: {
      flex: 1,
      height: 38,
      borderRadius: 10,
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.background,
      alignItems: 'center',
      justifyContent: 'center',
    },
    previewBox: {
      backgroundColor: colors.surfaceMuted,
      borderRadius: 12,
      padding: 12,
      marginTop: 4,
    },
    previewLabel: {
      fontSize: 11.5,
      fontWeight: '600',
      color: colors.muted,
    },
    previewValue: {
      fontSize: 15,
      fontWeight: '700',
      color: colors.text,
      marginTop: 3,
    },
    validationText: {
      fontSize: 13,
      fontWeight: '600',
      color: colors.danger,
    },
    footerRow: {
      flexDirection: 'row',
      gap: 10,
      paddingHorizontal: 18,
      paddingVertical: 14,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    cancelBtn: {
      flex: 1,
      paddingVertical: 13,
      borderRadius: 12,
      borderWidth: 1,
      borderColor: colors.border,
      alignItems: 'center',
      justifyContent: 'center',
    },
    cancelBtnText: {
      fontSize: 14,
      fontWeight: '600',
      color: colors.text,
    },
    confirmBtn: {
      flex: 1.5,
      paddingVertical: 13,
      borderRadius: 12,
      backgroundColor: colors.primary,
      alignItems: 'center',
      justifyContent: 'center',
    },
    confirmBtnText: {
      fontSize: 14,
      fontWeight: '700',
      color: colors.onPrimary,
    },
  });
