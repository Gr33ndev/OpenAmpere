import { useEffect, useRef, useState } from "react";

/** Easter egg: a secret code switches the energy flow into a retro game look. Purely cosmetic. */
export type EasterEggKey = "up" | "down" | "left" | "right" | "b" | "a";
export const EASTER_EGG_CODE: EasterEggKey[] = ["up", "up", "down", "down", "left", "right", "left", "right", "b", "a"];

const KEYS: Record<string, EasterEggKey> = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", b: "b", B: "b", a: "a", A: "a" };

/** How much of the code is entered after `key`: the longest end of the input that is a start of the code,
 * so "up up up down ..." still counts. */
export function advance(progress: number, key: EasterEggKey): number {
  const input = [...EASTER_EGG_CODE.slice(0, progress), key];
  for (let start = 0; start < input.length; start++) {
    if (input.slice(start).every((k, i) => EASTER_EGG_CODE[i] === k)) return input.length - start;
  }
  return 0;
}

function typing(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}

/** Arrow keys and B, A on the keyboard, or `press()` from taps. Returns how far the code is entered. */
export function useEasterEgg(onCode: () => void): { progress: number; press: (key: EasterEggKey) => void } {
  const progress = useRef(0);
  const [shown, setShown] = useState(0);
  const handler = useRef(onCode);
  handler.current = onCode;
  const press = useRef((key: EasterEggKey) => {
    const next = advance(progress.current, key);
    progress.current = next === EASTER_EGG_CODE.length ? 0 : next;
    setShown(progress.current);
    if (next === EASTER_EGG_CODE.length) handler.current();
  }).current;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const key = KEYS[e.key];
      if (!key || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      press(key);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [press]);
  return { progress: shown, press };
}

/** A few square-wave notes, like an old game console. */
export function chiptune(notes: number[]) {
  try {
    const ctx = new AudioContext();
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      const start = ctx.currentTime + i * 0.09;
      osc.type = "square";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.05, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.085);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.09);
    });
    setTimeout(() => ctx.close(), notes.length * 90 + 200);
  } catch {
    // no audio (old browser, blocked): the look changes anyway
  }
}

/** Whether the retro look is on (class "retro" on <html>), with a re-render when it changes. */
export function useRetroLook(): boolean {
  const [on, setOn] = useState(() => document.documentElement.classList.contains("retro"));
  useEffect(() => {
    const html = document.documentElement;
    const observer = new MutationObserver(() => setOn(html.classList.contains("retro")));
    observer.observe(html, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return on;
}
