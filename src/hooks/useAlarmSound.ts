/**
 * useAlarmSound: operator-controlled audible alarm notification.
 *
 * The sound is generated in the browser with the Web Audio API: no asset file,
 * no CDN, no new dependency. Browsers refuse to start audio before the page has
 * had a user gesture, so the AudioContext is created inside the toggle click and
 * inside testAlarm(), never on mount. Every call is best-effort: a missing or
 * blocked audio API degrades to a silent no-op and never throws.
 */

import { useCallback, useRef, useState } from "react";

const STORAGE_KEY = "nms.alarmSound.enabled";
/** At most one alarm beep per 5 s; the operator is not the only listener. */
const MIN_GAP_MS = 5000;
/**
 * The same alarm clearing and re-raising (a marginal threshold at dusk, a flapping
 * sensor) must not machine-gun the room: one alarm is audible once per window.
 */
const FLAP_WINDOW_MS = 5 * 60_000;
/** Bounded: an all-day session cannot accumulate one entry per alarm forever. */
const FLAP_MAP_MAX = 64;
const TONE_MS = 150;
const TONE_GAIN = 0.15;
const TONES_HIGH = [
  { offset: 0, frequency: 880 },
  { offset: 0.2, frequency: 660 },
] as const;
/** A disaster is not louder, it is different: the ear separates outage from warning. */
const TONES_DISASTER = [
  { offset: 0, frequency: 880 },
  { offset: 0.14, frequency: 660 },
  { offset: 0.28, frequency: 880 },
] as const;

/** Only the two severities an operator must not miss are audible. */
export function isAudibleSeverity(severity: string | undefined): boolean {
  return severity === "high" || severity === "disaster";
}

/** Throttle decision, kept pure so the 5 s rule is testable without a browser. */
export function alarmBeepAllowed(lastPlayedAt: number, now: number): boolean {
  return now - lastPlayedAt >= MIN_GAP_MS;
}

/** Per-alarm flap guard, pure so it is testable without a browser or an alarm id. */
export function flapSuppressed(lastForAlarmAt: number | undefined, now: number): boolean {
  return lastForAlarmAt !== undefined && now - lastForAlarmAt < FLAP_WINDOW_MS;
}

function readStoredEnabled(): boolean {
  if (typeof localStorage === "undefined") return false;
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function storeEnabled(value: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
  } catch {
    // Private mode or a full quota: the preference just does not persist.
  }
}

function createContext(): AudioContext | null {
  if (typeof window === "undefined" || typeof window.AudioContext === "undefined") return null;
  try {
    return new window.AudioContext();
  } catch {
    return null;
  }
}

function playTones(ctx: AudioContext, tones: ReadonlyArray<{ readonly offset: number; readonly frequency: number }>): void {
  const start = ctx.currentTime;
  for (const tone of tones) {
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    const at = start + tone.offset;
    const end = at + TONE_MS / 1000;
    oscillator.type = "sine";
    oscillator.frequency.value = tone.frequency;
    // Short fades, not a hard gate: a raw start/stop on a sine clicks.
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(TONE_GAIN, at + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, end);
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start(at);
    oscillator.stop(end);
  }
}

export interface AlarmSoundControls {
  enabled: boolean;
  setEnabled: (value: boolean) => void;
  playAlarm: (severity?: string, alarmId?: string) => void;
  testAlarm: () => void;
}

export function useAlarmSound(): AlarmSoundControls {
  const [enabled, setEnabledState] = useState(readStoredEnabled);
  const contextRef = useRef<AudioContext | null>(null);
  const lastPlayedRef = useRef(0);
  const lastPerAlarmRef = useRef<Map<string, number>>(new Map());

  const sound = useCallback((tones: ReadonlyArray<{ offset: number; frequency: number }> = TONES_HIGH): void => {
    try {
      const ctx = contextRef.current ?? createContext();
      if (!ctx) return;
      contextRef.current = ctx;
      // A suspended context is the expected state before, or long after, the
      // unlocking click; resume resolves only once the page has been touched.
      if (ctx.state !== "running") void ctx.resume().catch(() => {});
      playTones(ctx, tones);
    } catch {
      // Best-effort only: audio availability must never break alarm handling.
    }
  }, []);

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value);
    storeEnabled(value);
    // This click is the user gesture that unlocks audio; confirm it audibly.
    if (value) sound();
  }, [sound]);

  const testAlarm = useCallback((): void => {
    sound();
  }, [sound]);

  const playAlarm = useCallback((severity?: string, alarmId?: string): void => {
    if (!enabled || !isAudibleSeverity(severity)) return;
    const now = Date.now();
    if (!alarmBeepAllowed(lastPlayedRef.current, now)) return;
    const ceiling = lastPerAlarmRef.current;
    if (alarmId) {
      if (flapSuppressed(ceiling.get(alarmId), now)) return;
      if (ceiling.size >= FLAP_MAP_MAX) {
        for (const [key, at] of ceiling) {
          if (!flapSuppressed(at, now)) ceiling.delete(key);
        }
      }
      ceiling.set(alarmId, now);
    }
    lastPlayedRef.current = now;
    sound(severity === "disaster" ? TONES_DISASTER : TONES_HIGH);
  }, [enabled, sound]);

  return { enabled, setEnabled, playAlarm, testAlarm };
}
