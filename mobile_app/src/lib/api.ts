import type { LatLng, NearbyCluster } from './types';

// Empty = same origin. nginx (Docker) and the Vite dev server proxy /api to the backend.
const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

export interface CreateReportPayload {
  report_id: string;
  user_behavior: 'victim' | 'observer';
  can_communicate: boolean;
  latitude: number;
  longitude: number;
  event_define: string | null;
  event_capture: string | null;
  report_date: string;
}

export interface CreateReportResponse {
  id: string;
  report_id: string;
  nearby_cluster: NearbyCluster | null;
}

export interface ImageUploadResponse {
  image_path: string;
  exif: { gps: { latitude: number | null; longitude: number | null } } | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    throw new Error(`${res.status} ${await res.text()}`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export function createReport(payload: CreateReportPayload): Promise<CreateReportResponse> {
  return request('/api/reports/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function uploadImage(reportId: string, photo: File): Promise<ImageUploadResponse> {
  const form = new FormData();
  form.append('image', photo, photo.name || 'photo.jpg');
  return request(`/api/reports/${encodeURIComponent(reportId)}/image`, { method: 'POST', body: form });
}

/**
 * Browsers do not expose camera EXIF like the native app did, so when the server
 * finds no GPS in the uploaded photo we send the device position as a fallback.
 */
export function uploadExif(
  reportId: string,
  data: { location: LatLng; width?: number; height?: number; takenAt?: string },
): Promise<unknown> {
  return request(`/api/reports/${encodeURIComponent(reportId)}/exif`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      latitude: data.location.latitude,
      longitude: data.location.longitude,
      image_width: data.width,
      image_height: data.height,
      date_taken: data.takenAt,
    }),
  });
}

export function deleteReport(id: string): Promise<void> {
  return request(`/api/reports/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
