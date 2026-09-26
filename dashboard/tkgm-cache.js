// TKGM Tile Cache Manager
// Dashboard'da kullanım için cache ve preload sistemi

class TKGMCacheManager {
  constructor() {
    this.cacheEnabled = true;
    this.preloadInProgress = false;
    this.akyaziBounds = {
      lat_min: 40.975135,
      lat_max: 40.985795,
      lon_min: 37.900106,
      lon_max: 37.934134
    };
    
    this.init();
  }
  
  async init() {
    // Service Worker'ı register et
    if ('serviceWorker' in navigator) {
      try {
        const registration = await navigator.serviceWorker.register('./tkgm-cache-worker.js');
        console.log('[TKGM Cache] Service worker registered:', registration);
        
        // Service worker hazır olunca cache preload başlat
        if (registration.active) {
          this.startPreload();
        } else {
          registration.addEventListener('statechange', () => {
            if (registration.active) {
              this.startPreload();
            }
          });
        }
      } catch (error) {
        console.warn('[TKGM Cache] Service worker registration failed:', error);
      }
    }
    
    // Cache storage boyutunu kontrol et
    this.monitorCacheSize();
  }
  
  async startPreload() {
    if (this.preloadInProgress) return;
    this.preloadInProgress = true;
    
    console.log('[TKGM Cache] Starting Akyazı tile preload...');
    
    // Akyazı alanındaki kritik tile'ları önceden yükle
    const tilesToPreload = this.getAkyaziTileUrls();
    
    let loaded = 0;
    const total = tilesToPreload.length;
    
    for (const tileUrl of tilesToPreload) {
      try {
        const response = await fetch(tileUrl, {
          method: 'GET',
          cache: 'force-cache' // Browser cache'ini zorla kullan
        });
        
        if (response.ok) {
          loaded++;
          console.log(`[TKGM Cache] Preloaded ${loaded}/${total}: ${tileUrl}`);
        }
      } catch (error) {
        console.warn('[TKGM Cache] Preload failed for:', tileUrl, error);
      }
      
      // Her 5 tile'dan sonra 100ms bekle (rate limiting)
      if (loaded % 5 === 0) {
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    }
    
    console.log(`[TKGM Cache] Preload completed: ${loaded}/${total} tiles cached`);
    this.preloadInProgress = false;
    
    // Preload completion event'i gönder
    window.dispatchEvent(new CustomEvent('tkgm-cache-ready', {
      detail: { cachedTiles: loaded, totalTiles: total }
    }));
  }
  
  getAkyaziTileUrls() {
    const baseUrls = [
      'https://3dsurectakipservis.tkgm.gov.tr/TileService/Texture',
      'https://3dsurectakipservis.tkgm.gov.tr/TileService/Solid',
      'https://3dsurectakipservis.tkgm.gov.tr/TileService/MimariSolid'
    ];
    
    const tiles = [];
    
    // Akyazı alanını kapsayan tile'lar (önceki analiz sonucundan)
    const tileCoords = [
      // Level 6
      { level: 6, x: 38, y: 23 },
      { level: 6, x: 38, y: 24 },
      
      // Level 7
      { level: 7, x: 76, y: 47 },
      { level: 7, x: 77, y: 47 },
      { level: 7, x: 76, y: 48 },
      { level: 7, x: 77, y: 48 },
      
      // Level 8
      { level: 8, x: 154, y: 95 },
      { level: 8, x: 154, y: 96 },
      { level: 8, x: 155, y: 95 },
      { level: 8, x: 155, y: 96 }
    ];
    
    for (const coord of tileCoords) {
      for (const baseUrl of baseUrls) {
        tiles.push(`${baseUrl}/${coord.level}/${coord.x}/${coord.y}.json`);
      }
    }
    
    return tiles;
  }
  
  async getCacheStats() {
    if ('storage' in navigator && 'estimate' in navigator.storage) {
      const estimate = await navigator.storage.estimate();
      return {
        usedBytes: estimate.usage || 0,
        availableBytes: estimate.quota || 0,
        usedMB: Math.round((estimate.usage || 0) / 1024 / 1024),
        availableMB: Math.round((estimate.quota || 0) / 1024 / 1024)
      };
    }
    return null;
  }
  
  async monitorCacheSize() {
    const stats = await this.getCacheStats();
    if (stats) {
      console.log(`[TKGM Cache] Storage: ${stats.usedMB}MB / ${stats.availableMB}MB`);
      
      // Cache boyutu 100MB'ı aştıysa uyar
      if (stats.usedMB > 100) {
        console.warn('[TKGM Cache] Cache size is getting large, consider cleanup');
      }
    }
  }
  
  async clearCache() {
    if ('caches' in window) {
      const cacheNames = await caches.keys();
      for (const cacheName of cacheNames) {
        if (cacheName.includes('tkgm')) {
          await caches.delete(cacheName);
          console.log('[TKGM Cache] Cleared cache:', cacheName);
        }
      }
    }
  }
  
  // Cesium tileset load event'ine hook
  onTilesetLoaded(tileset) {
    if (!tileset || !this.cacheEnabled) return;
    
    // Tileset'e cache-friendly ayarlar uygula
    if ('maximumMemoryUsage' in tileset) tileset.maximumMemoryUsage = 256;
    tileset.preloadWhenHidden = true; // Background preload
    
    console.log('[TKGM Cache] Applied cache-friendly settings to tileset');
  }
}

// Global instance
window.tkgmCache = new TKGMCacheManager();