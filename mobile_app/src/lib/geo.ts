import type { LatLng } from './types';

export class LocationError extends Error {}

function getPosition(options: PositionOptions): Promise<LatLng> {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ latitude: p.coords.latitude, longitude: p.coords.longitude }),
      reject,
      options,
    );
  });
}

/**
 * Fresh device location: fast network-based fix first, then high-accuracy GPS.
 * Browsers only allow geolocation on HTTPS or localhost.
 */
export async function getCurrentLocation(): Promise<LatLng> {
  if (!('geolocation' in navigator) || !window.isSecureContext) {
    throw new LocationError('Tarayıcı konum erişimine izin vermiyor (HTTPS veya localhost gerekir).');
  }
  try {
    return await getPosition({ enableHighAccuracy: false, timeout: 20000, maximumAge: 0 });
  } catch {
    try {
      return await getPosition({ enableHighAccuracy: true, timeout: 30000, maximumAge: 0 });
    } catch (e) {
      const err = e as GeolocationPositionError;
      const reason =
        err.code === err.PERMISSION_DENIED
          ? 'Konum izni reddedildi.'
          : err.code === err.POSITION_UNAVAILABLE
            ? 'Konum servisi kapalı veya konum bulunamadı.'
            : 'Konum alınırken zaman aşımı oluştu.';
      throw new LocationError(reason);
    }
  }
}
