/** Removes emojis/special chars and collapses spam-like repetition. */
export function sanitizeInput(text: string): string {
  let clean = text.replace(
    /[\u{1F000}-\u{1FFFF}\u{2600}-\u{27BF}\u{1F300}-\u{1F9FF}\u{FE00}-\u{FEFF}\u{200B}-\u{200D}﻿]/gu,
    '',
  );
  // Letters (any language), numbers, whitespace and basic punctuation only
  clean = clean.replace(/[^\p{L}\p{N}\s.,!?'"\-()@]/gu, '');
  // "aaaaaaa" → "aaa"
  clean = clean.replace(/(.)\1{3,}/g, '$1$1$1');
  // "yangın yangın yangın" → "yangın yangın"
  clean = clean.replace(/\b(\w+)(\s+\1){2,}\b/gi, '$1 $1');
  clean = clean.replace(/[ \t]{2,}/g, ' ');
  clean = clean.replace(/\n{3,}/g, '\n\n');
  return clean;
}

/** Readable report id: RPT-YYYYMMDD-XXXX */
export function generateReportId(): string {
  const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const rand = Math.floor(Math.random() * 9000) + 1000;
  return `RPT-${dateStr}-${rand}`;
}
