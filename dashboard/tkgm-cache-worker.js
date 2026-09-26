// TKGM 3D Tiles Cache Service Worker
// Bu worker TKGM tile request'lerini yakalayıp cache'ler

const CACHE_NAME = 'tkgm-tiles-v1';
const CACHE_DURATION = 7 * 24 * 60 * 60 * 1000; // 7 gün

// Akyazı bounding box (cache'lenecek alan)
const AKYAZI_BBOX = {
  lat_min: 40.975135,
  lat_max: 40.985795,
  lon_min: 37.900106,
  lon_max: 37.934134
};

self.addEventListener('install', event => {
  console.log('[TKGM Cache] Service worker installing');
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  console.log('[TKGM Cache] Service worker activating');
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  
  // Sadece TKGM tile request'lerini yakala
  if (isTKGMTileRequest(url)) {
    event.respondWith(handleTKGMRequest(event.request));
  }
});

function isTKGMTileRequest(url) {
  return url.hostname === '3dsurectakipservis.tkgm.gov.tr' ||
         url.hostname === '3dbina.tkgm.gov.tr';
}

async function handleTKGMRequest(request) {
  const cache = await caches.open(CACHE_NAME);
  const cacheKey = request.url;
  
  // Cache'den kontrol et
  const cachedResponse = await cache.match(cacheKey);
  if (cachedResponse) {
    const cacheDate = new Date(cachedResponse.headers.get('sw-cache-date'));
    const now = new Date();
    
    // Cache geçerli mi?
    if (now - cacheDate < CACHE_DURATION) {
      console.log('[TKGM Cache] Serving from cache:', request.url);
      return cachedResponse;
    } else {
      // Eski cache'i sil
      await cache.delete(cacheKey);
    }
  }
  
  // Network'den fetch et
  try {
    const response = await fetch(request);
    
    if (response.ok) {
      // Response'u clone'la (stream bir kere okunabilir)
      const responseToCache = response.clone();
      
      // Akyazı alanında mı kontrol et (tile URL'den koordinat parse et)
      if (isInAkyaziArea(request.url)) {
        // Cache'e kaydet
        const responseWithHeaders = new Response(await responseToCache.arrayBuffer(), {
          status: response.status,
          statusText: response.statusText,
          headers: {
            ...Object.fromEntries(response.headers.entries()),
            'sw-cache-date': new Date().toISOString()
          }
        });
        
        await cache.put(cacheKey, responseWithHeaders);
        console.log('[TKGM Cache] Cached tile:', request.url);
      }
    }
    
    return response;
  } catch (error) {
    console.warn('[TKGM Cache] Network failed, checking cache fallback:', error);
    
    // Network fail, cache'den dön (süresi geçmiş olsa bile)
    const fallbackResponse = await cache.match(cacheKey);
    if (fallbackResponse) {
      console.log('[TKGM Cache] Serving stale cache for:', request.url);
      return fallbackResponse;
    }
    
    throw error;
  }
}

function isInAkyaziArea(tileUrl) {
  // TKGM tile URL formatı: .../TileService/Texture/6/38/23.json
  // veya: .../TileService/Texture/8/154/95.json
  
  try {
    const urlParts = tileUrl.split('/');
    const level = parseInt(urlParts[urlParts.length - 3]);
    const x = parseInt(urlParts[urlParts.length - 2]);
    const y = parseInt(urlParts[urlParts.length - 1].split('.')[0]);
    
    // Sadece Akyazı'ya yakın tile'ları cache'le
    // Level 8: X=154, Y=95-96 arası
    // Level 6: X=38, Y=23-24 arası
    if (level === 8 && x === 154 && (y >= 95 && y <= 96)) return true;
    if (level === 6 && x === 38 && (y >= 23 && y <= 24)) return true;
    if (level >= 9 && level <= 15) {
      // Yüksek zoom seviyelerinde de Akyazı alanı içindeyse cache'le
      // Bu basit check, gerçek bounding box hesabı için daha kompleks olabilir
      if (x >= 150 && x <= 160 && y >= 90 && y <= 100) return true;
    }
    
    return false;
  } catch (e) {
    // Parse hatası, her ihtimale cache'le
    return true;
  }
}

// Cache temizleme fonksiyonu (periodically call)
async function cleanupOldCache() {
  const cache = await caches.open(CACHE_NAME);
  const keys = await cache.keys();
  const now = new Date();
  
  for (const request of keys) {
    const response = await cache.match(request);
    if (response) {
      const cacheDate = new Date(response.headers.get('sw-cache-date'));
      if (now - cacheDate > CACHE_DURATION) {
        await cache.delete(request);
        console.log('[TKGM Cache] Cleaned up old cache:', request.url);
      }
    }
  }
}

// Her saatte bir cleanup çalıştır
setInterval(cleanupOldCache, 60 * 60 * 1000);