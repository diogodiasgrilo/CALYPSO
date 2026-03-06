/** Web Audio API sound effects for trading events. */

let audioCtx: AudioContext | null = null;
let muted = localStorage.getItem("calypso-muted") === "true";

function getCtx(): AudioContext {
  if (!audioCtx) audioCtx = new AudioContext();
  return audioCtx;
}

function playTone(freq: number, duration: number, type: OscillatorType = "sine") {
  if (muted) return;
  const ctx = getCtx();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.15, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
  osc.connect(gain).connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + duration);
}

/** Entry placed — single soft chime (C5). */
export function playEntryPlaced() {
  playTone(523.25, 0.15);
}

/** Stop triggered — two-tone descending (A3→G3). */
export function playStopTriggered() {
  playTone(220, 0.2);
  setTimeout(() => playTone(196, 0.2), 200);
}

/** Entry expired (profit) — ascending two-tone (C5→E5). */
export function playEntryExpired() {
  playTone(523.25, 0.1);
  setTimeout(() => playTone(659.25, 0.1), 100);
}

export function isMuted(): boolean {
  return muted;
}

export function toggleMute(): boolean {
  muted = !muted;
  localStorage.setItem("calypso-muted", String(muted));
  return muted;
}
