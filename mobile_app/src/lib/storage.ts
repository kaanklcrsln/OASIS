import type { Report } from './types';

const REPORTS_KEY = '@oasis_reports';

export function loadReports(): Report[] {
  try {
    const raw = localStorage.getItem(REPORTS_KEY);
    return raw ? (JSON.parse(raw) as Report[]) : [];
  } catch (e) {
    console.warn('Failed to load reports:', e);
    return [];
  }
}

export function saveReports(reports: Report[]): void {
  try {
    localStorage.setItem(REPORTS_KEY, JSON.stringify(reports));
  } catch (e) {
    console.warn('Failed to save reports:', e);
  }
}
