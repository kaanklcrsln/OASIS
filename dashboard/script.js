// ============================================================
// TKGM 3B Bina - Cesium Uygulaması
// Tüm servisler TKGM'nin kendi sunucularından gelir:
//   - Terrain  : terrain.tkgm.gov.tr
//   - Ortofoto : ortofoto.tkgm.gov.tr
//   - 3D Tiles : 3dsurectakipservis.tkgm.gov.tr
// ============================================================

// Çalışma zamanı ayarları — nginx /config.js üzerinden .env'den gelir (bkz. nginx/default.conf.template)
const OASIS_CONFIG = window.OASIS_CONFIG || {};

// Cesium ion token (opsiyonel). Tanımlı değilse CesiumJS'in varsayılan token'ı kullanılır.
if (OASIS_CONFIG.cesiumIonToken) {
  Cesium.Ion.defaultAccessToken = OASIS_CONFIG.cesiumIonToken;
}

// -------- TKGM Servis URL'leri --------
// Nginx proxy üzerinden → CORS sorunu çözümü + browser cache
const _TKGM_BASE = window.location.origin;
const TKGM = {
  terrain:          'https://terrain.tkgm.gov.tr/tilesets/tile/',
  tileTexture:      `${_TKGM_BASE}/tkgm-proxy/surec/TileService/Texture/5/tileset.json`,
  tileSolid:        `${_TKGM_BASE}/tkgm-proxy/surec/TileService/Solid/5/tileset.json`,
  tileMimariSolid:  `${_TKGM_BASE}/tkgm-proxy/surec/TileService/MimariSolid/5/tileset.json`,
  tileIndependent:  `${_TKGM_BASE}/tkgm-proxy/bina/IndependentSectionTile/tileset.json`,
};

var viewer = null;
var activeLayers = {};   // key → primitive referansı
var _labelsLayer = null; // Esri etiket katmanı referansı
var _activeTerrain = 'flat';  // 'flat' | 'cesium' | 'tkgm'
var _skipFirstZoom = false;   // initViewer'dan texture yüklenirken zoomTo'yu atla

// ============================================================
// 1. VIEWER BAŞLATMA
// ============================================================
async function initViewer() {
  try {
    viewer = new Cesium.Viewer('cesiumContainer', {
      scene3DOnly:          true,
      baseLayerPicker:      false,
      homeButton:           false,
      fullscreenButton:     false,
      infoBox:              false,
      selectionIndicator:   false,
      geocoder:             false,
      animation:            false,
      timeline:             false,
      navigationHelpButton: false,
      sceneModePicker:      false,
      // Sadece değişiklik olduğunda render et → CPU/GPU tasarrufu
      requestRenderMode:         true,
      maximumRenderTimeChange:   0.5,   // 0.5sn'de bir otomatik render (Infinity ise ilk frame boş kalıyor)
    });

    // ---- Altlık harita: Bing Aerial (önce dene) → OSM (fallback) ----
    viewer.imageryLayers.removeAll();

    // Önce ESRI World Imagery (ücretsiz, token gerekmez, yüksek kalite uydu)
    viewer.imageryLayers.add(
      new Cesium.ImageryLayer(
        new Cesium.UrlTemplateImageryProvider({
          url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
          maximumLevel: 18,
          credit: 'Esri World Imagery',
        })
      )
    );

    // Üzerine ESRI etiket katmanı (yol/yer adları)
    _labelsLayer = viewer.imageryLayers.add(
      new Cesium.ImageryLayer(
        new Cesium.UrlTemplateImageryProvider({
          url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
          maximumLevel: 18,
          credit: 'Esri Labels',
        })
      )
    );
    updateLayerBtn('labels', true);

    // ---- Globe görünürlük garantisi ----
    viewer.scene.globe.show                  = true;
    viewer.scene.globe.baseColor             = Cesium.Color.DARKBLUE;

    // ---- Başlangıçta düz (Ellipsoid) terrain — Entity'ler garanti görünür ----
    viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
    _activeTerrain = 'flat';

    // ---- depthTest: terrain varken binaları düzgün göster ----
    viewer.scene.globe.depthTestAgainstTerrain = false;  // flat modda kapalı

    // ---- Globe tile yükleme optimizasyonu ----
    // Sadece kameranın baktığı bölgeyi indir, tüm dünyayı değil
    viewer.scene.globe.maximumScreenSpaceError   = 4;     // varsayılan 2 → azaltınca daha az tile
    viewer.scene.globe.tileCacheSize             = 100;   // belleği sınırla
    viewer.scene.globe.preloadAncestors          = false; // ata tile'ları önceden indirme
    viewer.scene.globe.preloadSiblings           = false; // komşu tile'ları önceden indirme

    // ---- Görsel ayarlar ----
    viewer.scene.globe.enableLighting       = false; // kapalı → her frame'de ışık hesabı yok
    viewer.scene.shadowMap.enabled          = false;
    viewer.scene.fog.density                = 0.0003; // sis biraz daha yoğun → uzak tile'lar gizlenir
    viewer.scene.fog.minimumBrightness      = 0.0;
    viewer.scene.globe.showGroundAtmosphere = true;
    viewer.scene.skyAtmosphere.show         = true;
    viewer.scene.backgroundColor            = Cesium.Color.BLACK;

    // ---- Render kalite / hız dengesi ----
    viewer.scene.postProcessStages.fxaa.enabled = false; // anti-aliasing kapat → hız artışı
    viewer.resolutionScale                      = 1.0;   // retina'da 0.75 yapılabilir

    // ---- Ordu/Akyazı başlangıç ----
    viewer.camera.setView({
      destination: Cesium.Cartesian3.fromDegrees(37.91462, 40.97621, 539),
      orientation: {
        heading: 0,
        pitch:   Cesium.Math.toRadians(-45),
        roll:    0,
      },
    });
    viewer.scene.requestRender();   // ilk frame'i hemen çiz

    // Pencere boyutu değiştiğinde Cesium canvas'ı yeniden boyutla
    window.addEventListener('resize', () => {
      if (viewer) { viewer.resize(); viewer.scene.requestRender(); }
    });

    setupCoordinateTracking();
    setupPickingHandler();
    console.log('✅ Viewer hazır');

    // Textureli binalar varsayılan olarak açık gelsin
    // _skipFirstZoom: ilk katman yüklendiğinde zoomTo yapma, kamera zaten Ordu'da
    _skipFirstZoom = true;
    toggleTexture();
  } catch (err) {
    console.error('❌ Viewer hatası:', err);
    document.body.innerHTML =
      '<div style="color:red;padding:30px;font-size:18px;">Cesium yüklenemedi: ' + err.message + '</div>';
  }
}

initViewer();

// ============================================================
// 2. TKGM 3D TILE KATMANI YÜKLE
// ============================================================
async function loadTKGMLayer(key, url, label) {
  showLoading(true);
  try {
    // Zaten yüklüyse kaldır (toggle)
    if (activeLayers[key]) {
      viewer.scene.primitives.remove(activeLayers[key]);
      delete activeLayers[key];
      updateLayerBtn(key, false);
      showLoading(false);
      return;
    }

    const tileset = await Cesium.Cesium3DTileset.fromUrl(url, {
      maximumScreenSpaceError: 4,    // Yüksek kalite
      skipLevelOfDetail: false,
      memoryAdjustedScreenSpaceError: 2.0, // Cache optimization
      preloadWhenHidden: true,
    });

    // Cache manager'a tileset'i bildir
    if (window.tkgmCache) {
      window.tkgmCache.onTilesetLoaded(tileset);
    }

    // Textureli katmanda orijinal rengi koru
    if (key !== 'texture') {
      tileset.style = new Cesium.Cesium3DTileStyle({
        color: key === 'solid'
          ? "color('#c8a97e', 1.0)"        // krem/bej
          : key === 'mimari'
            ? "color('#a8c8e8', 0.9)"      // açık mavi
            : "color('#ffffff', 1.0)",
      });
    }

    viewer.scene.primitives.add(tileset);
    activeLayers[key] = tileset;
    updateLayerBtn(key, true);

    // Google katmanı aktifse TKGM'yi yukarı kaldır
    if (_google3DTileset) _applyHeightOffsetToTileset(tileset, TKGM_HEIGHT_OFFSET);

    // Aktif rapor zone'ları varsa bu yeni tileset'e de stili uygula
    if (_buildingZones && _buildingZones.length > 0) {
      _styleOneTileset(tileset);
    }

    // İlk katman yüklenince oraya uç (ama initViewer'dan geldiyse atla)
    if (Object.keys(activeLayers).length === 1 && !_skipFirstZoom) {
      await viewer.zoomTo(
        tileset,
        new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-40), 500)
      );
    }
    _skipFirstZoom = false;

    showLoading(false);
    console.log('✅', label, 'yüklendi');
  } catch (err) {
    showLoading(false);
    showError(label + ' yüklenemedi: ' + err.message);
    console.error(err);
  }
}

// Buton kısayolları
function toggleTexture()     { loadTKGMLayer('texture',  TKGM.tileTexture,     'Textureli Binalar'); }
function toggleSolid()       { loadTKGMLayer('solid',    TKGM.tileSolid,       'Solid Binalar'); }
function toggleMimari()      { loadTKGMLayer('mimari',   TKGM.tileMimariSolid, 'Mimari Solid'); }
function toggleIndependent() { loadTKGMLayer('section',  TKGM.tileIndependent, 'Bağımsız Bölüm'); }

// ============================================================
// ARAZİ MODELİ DEĞİŞTİRME (Terrain Toggle)
// ============================================================
async function setTerrain(mode) {
  if (!viewer) return;
  showLoading(true);

  // Buton stillerini güncelle
  ['flat', 'cesium', 'tkgm'].forEach(m => {
    const btn = document.getElementById('btn-terrain-' + m);
    if (btn) btn.classList.toggle('active-layer', m === mode);
  });

  try {
    if (mode === 'flat') {
      viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
      viewer.scene.globe.depthTestAgainstTerrain = false;
      console.log('🌍 Düz terrain (Ellipsoid)');

    } else if (mode === 'cesium') {
      viewer.terrainProvider = await Cesium.CesiumTerrainProvider.fromUrl(
        Cesium.IonResource.fromAssetId(1), // Cesium World Terrain
        { requestVertexNormals: false, requestWaterMask: false }
      );
      viewer.scene.globe.depthTestAgainstTerrain = true;
      console.log('🏔️ Cesium World Terrain');

    } else if (mode === 'tkgm') {
      viewer.terrainProvider = await Cesium.CesiumTerrainProvider.fromUrl(
        TKGM.terrain,
        { requestVertexNormals: false, requestWaterMask: false }
      );
      viewer.scene.globe.depthTestAgainstTerrain = true;
      console.log('🏔️ TKGM Terrain');
    }

    _activeTerrain = mode;
  } catch (e) {
    console.warn('⚠️ Terrain yüklenemedi:', e.message, '→ Ellipsoid fallback');
    viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
    viewer.scene.globe.depthTestAgainstTerrain = false;
    _activeTerrain = 'flat';
    ['flat', 'cesium', 'tkgm'].forEach(m => {
      const btn = document.getElementById('btn-terrain-' + m);
      if (btn) btn.classList.toggle('active-layer', m === 'flat');
    });
  }

  viewer.scene.requestRender();
  showLoading(false);
}

// Etiket katmanını aç/kapat
function toggleLabels() {
  if (!viewer) return;
  if (_labelsLayer) {
    viewer.imageryLayers.remove(_labelsLayer);
    _labelsLayer = null;
    updateLayerBtn('labels', false);
  } else {
    _labelsLayer = viewer.imageryLayers.add(
      new Cesium.ImageryLayer(
        new Cesium.UrlTemplateImageryProvider({
          url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
          maximumLevel: 19,
          credit: 'Esri Labels',
        })
      )
    );
    updateLayerBtn('labels', true);
  }
}

// ============================================================
// ============================================================
// 3. ÖZEL TILESET YÜKLE (GET ile kendi sunucudan)
// ============================================================
async function loadCustomTileset() {
  const url = document.getElementById('tilesetUrl').value.trim();
  if (!url) { alert("Lütfen Tileset URL'sini girin"); return; }

  showLoading(true);
  try {
    if (activeLayers['custom']) {
      viewer.scene.primitives.remove(activeLayers['custom']);
      delete activeLayers['custom'];
    }

    const tileset = await Cesium.Cesium3DTileset.fromUrl(url, {
      maximumScreenSpaceError: 4,
    });

    viewer.scene.primitives.add(tileset);
    activeLayers['custom'] = tileset;

    await viewer.zoomTo(
      tileset,
      new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), 500)
    );

    showLoading(false);
    console.log('✅ Özel Tileset yüklendi:', url);
  } catch (err) {
    showLoading(false);
    showError('Tileset yüklenemedi: ' + err.message);
  }
}

// ============================================================
// 4. TÜM KATMANLARI KALDIR
// ============================================================
function clearAllLayers() {
  Object.keys(activeLayers).forEach(key => {
    viewer.scene.primitives.remove(activeLayers[key]);
  });
  activeLayers = {};
  ['texture','solid','mimari','section','google'].forEach(k => updateLayerBtn(k, false));
  // Bina renk zone'larını da temizle
  clearBuildingZones();
  // Yol katmanını temizle
  if (_roadLayerEnabled) {
    _roadLayerEnabled = false;
    clearRoadLayer();
    updateLayerBtn('roads', false);
    _updateRoadStats(0, 0, 0);
    const rp = document.getElementById('road-status-panel');
    if (rp) rp.style.display = 'none';
  }
  // Sel simülasyonu temizle
  if (_floodEnabled) {
    _floodEnabled = false;
    clearFloodLayer();
    const fp = document.getElementById('floodInline');
    if (fp) fp.classList.remove('open');
    const fb = document.getElementById('btn-flood');
    if (fb) fb.classList.remove('active-layer');
    _floodWaterHeight = 0;
    const slider = document.getElementById('floodSlider');
    if (slider) slider.value = 0;
    const valEl = document.getElementById('floodLevelVal');
    if (valEl) valEl.firstChild.textContent = '0.0';
  }
  viewer.scene.globe.show = true;
  viewer.scene.globe.depthTestAgainstTerrain = true;
  viewer.scene.requestRender();
}

// ============================================================
// 5. KONUMA GİT (Enter veya buton)
// ============================================================
function flyToCoords() {
  const lat = parseFloat(document.getElementById('latitude').value)  || 39.9334;
  const lng = parseFloat(document.getElementById('longitude').value) || 32.8597;
  const alt = parseFloat(document.getElementById('altitude').value)  || 500;
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(lng, lat, alt),
    orientation: { pitch: Cesium.Math.toRadians(-35), heading: 0, roll: 0 },
    duration: 2,
  });
}

['latitude','longitude','altitude'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('keypress', e => {
    if (e.key === 'Enter') flyToCoords();
  });
});

// ============================================================
// 6. GÖRÜNÜMÜ SIFIRLA
// ============================================================
function resetView() {
  clearAllLayers();

  // Altlık haritayı geri yükle
  viewer.imageryLayers.removeAll();
  viewer.imageryLayers.add(
    new Cesium.ImageryLayer(
      new Cesium.UrlTemplateImageryProvider({
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        maximumLevel: 18,
        credit: 'Esri World Imagery',
      })
    )
  );
  viewer.imageryLayers.add(
    new Cesium.ImageryLayer(
      new Cesium.UrlTemplateImageryProvider({
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
        maximumLevel: 18,
        credit: 'Esri Labels',
      })
    )
  );

  _labelsLayer = viewer.imageryLayers.get(viewer.imageryLayers.length - 1);
  updateLayerBtn('labels', true);

  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(35.0, 39.0, 1200000),
    orientation: { pitch: Cesium.Math.toRadians(-55), heading: 0, roll: 0 },
    duration: 2,
  });
}

// ============================================================
// 7. KOORDİNAT TAKİBİ + HEADING GÖSTERGESİ
// ============================================================
const HEADING_LABELS = [
  'K','KKD','KD','DKD','D','DGD','GD','GGD',
  'G','GGB','GB','BGB','B','KBB','KB','KKB','K'
];

function setupCoordinateTracking() {
  viewer.scene.postRender.addEventListener(() => {
    try {
      const c = Cesium.Cartographic.fromCartesian(viewer.camera.position);
      const lat = Cesium.Math.toDegrees(c.latitude).toFixed(5);
      const lon = Cesium.Math.toDegrees(c.longitude).toFixed(5);
      const alt = Math.round(c.height).toLocaleString();
      // Old coordBar
      const el1 = document.getElementById('curLat'); if (el1) el1.textContent = lat;
      const el2 = document.getElementById('curLng'); if (el2) el2.textContent = lon;
      const el3 = document.getElementById('curAlt'); if (el3) el3.textContent = alt;
      // New camControl coord bar
      const cl = document.getElementById('camLat'); if (cl) cl.textContent = lat;
      const co = document.getElementById('camLon'); if (co) co.textContent = lon;
      const ca = document.getElementById('camAlt'); if (ca) ca.textContent = alt;
      // Orientation (heading / pitch / roll)
      const hDeg = Cesium.Math.toDegrees(viewer.camera.heading).toFixed(1);
      const pDeg = Cesium.Math.toDegrees(viewer.camera.pitch).toFixed(1);
      const rDeg = Cesium.Math.toDegrees(viewer.camera.roll).toFixed(1);
      const ch = document.getElementById('camHead'); if (ch) ch.textContent = hDeg;
      const cp = document.getElementById('camPitch'); if (cp) cp.textContent = pDeg;
      const cr = document.getElementById('camRoll'); if (cr) cr.textContent = rDeg;
    } catch (_) {}
  });
}

// ============================================================
// 8. BİNA SEÇİMİ - TIKLAMA
// ============================================================
function setupPickingHandler() {
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

  handler.setInputAction(click => {
    if (!document.getElementById('enablePicking').checked) return;

    const picked = viewer.scene.pick(click.position);
    const panel  = document.getElementById('infoPanel');

    if (Cesium.defined(picked) && picked instanceof Cesium.Cesium3DTileFeature) {
      const props = {};
      picked.getPropertyIds().forEach(id => {
        props[id] = picked.getProperty(id);
      });

      // TKGM özelliklerini göster
      const rows = Object.entries(props)
        .filter(([k, v]) => v !== null && v !== undefined && v !== '')
        .map(([k, v]) => `<tr><td style="color:#888;padding:2px 8px 2px 0;font-size:11px;">${k}</td><td style="font-size:11px;font-weight:500;">${v}</td></tr>`)
        .join('');

      document.getElementById('buildingProps').innerHTML =
        rows || '<tr><td colspan="2" style="font-size:11px;color:#888;">Özellik bulunamadı</td></tr>';

      panel.style.display = 'block';
    } else {
      panel.style.display = 'none';
    }
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
}

// ============================================================
// YARDIMCI FONKSİYONLAR
// ============================================================
function showLoading(show) {
  document.getElementById('loadingIndicator').style.display = show ? 'block' : 'none';
}

function showError(msg) {
  const panel = document.getElementById('infoPanel');
  panel.innerHTML = '<h3 style="color:var(--a);">Hata</h3><p style="color:var(--t);font-size:12px;">' + msg + '</p><br>' +
    '<button class="action-btn danger" style="margin:0" onclick="document.getElementById(\'infoPanel\').style.display=\'none\'">Kapat</button>';
  panel.style.display = 'block';
}

function updateLayerBtn(key, active) {
  const btn = document.getElementById('btn-' + key);
  if (!btn) return;
  btn.classList.toggle('active-layer', active);
  const tgl = btn.querySelector('.rs-toggle');
  if (tgl) tgl.classList.toggle('on', active);
}

// ============================================================
// SIDEBAR & SEKME KONTROLU
// ============================================================
function switchSbTab(tab) {
  ['disaster', 'reports', 'layers'].forEach(t => {
    const btn = document.getElementById('sbtab-' + t);
    const pane = document.getElementById('pane-' + t);
    if (btn) btn.classList.toggle('active', tab === t);
    if (pane) pane.classList.toggle('active', tab === t);
  });
  if (tab === 'reports') {
    // Raporlar sekmesi: pipeline pinlerini göster, afet pinlerini gizle
    renderPipelineList();
    renderPipelinePins();
  } else {
    // Diğer sekmeler: pipeline pinlerini temizle, afet pinlerini geri göster
    clearPipelineEntities();
    _disasterEntities.forEach(e => { try { e.show = true; } catch {} });
    _clusterEntities.forEach(e => { try { e.show = true; } catch {} });
    if (viewer) viewer.scene.requestRender();
  }
}

// ============================================================
// OSM + ESRI UYDU TOGGLE
// ============================================================
var _osmLayer = null;

function toggleOSM() {
  if (!viewer) return;

  // Mevcut OSM katmanını URL'den bul
  const layers = viewer.imageryLayers;
  let found = null;
  for (let i = 0; i < layers.length; i++) {
    const l = layers.get(i);
    try {
      const url = (l.imageryProvider._url || l.imageryProvider.url || '');
      if (url.includes('openstreetmap.org')) { found = l; break; }
    } catch {}
  }

  if (found) {
    viewer.imageryLayers.remove(found, true);
    _osmLayer = null;
    updateLayerBtn('osm', false);
  } else {
    _osmLayer = viewer.imageryLayers.add(
      new Cesium.ImageryLayer(
        new Cesium.UrlTemplateImageryProvider({
          url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
          maximumLevel: 19,
          credit: 'OpenStreetMap contributors',
        })
      )
    );
    updateLayerBtn('osm', true);
  }
  viewer.scene.requestRender();
}

var _esriRemoved = false;
function toggleEsri() {
  if (!viewer) return;
  const layers = viewer.imageryLayers;
  // ESRI URL’si içeren katmanı bul
  let found = null;
  for (let i = 0; i < layers.length; i++) {
    const l = layers.get(i);
    try {
      const url = (l.imageryProvider._url || l.imageryProvider.url || '');
      if (url.includes('arcgisonline')) { found = l; break; }
    } catch {}
  }
  if (found) {
    viewer.imageryLayers.remove(found, false);
    _esriRemoved = true;
    updateLayerBtn('esri', false);
  } else {
    viewer.imageryLayers.add(
      new Cesium.ImageryLayer(
        new Cesium.UrlTemplateImageryProvider({
          url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
          maximumLevel: 18,
          credit: 'Esri World Imagery',
        })
      )
    );
    _esriRemoved = false;
    updateLayerBtn('esri', true);
  }
  viewer.scene.requestRender();
}

// ============================================================
// GOOGLE PHOTOREALISTIC 3D TILES
// ============================================================
// API key al: https://console.cloud.google.com/ → Map Tiles API enable → Credentials → Create API Key
// .env → GOOGLE_MAPS_API_KEY (opsiyonel). Tanımlı değilse bu katman devre dışıdır.
const GOOGLE_3D_TILES_API_KEY = OASIS_CONFIG.googleMapsApiKey || '';
// TKGM bina bölgesi — Google tileset bu bbox içinde kesilir, TKGM binalar görünür
const TKGM_CLIP_BBOX = {
  centerLon: 37.91462,
  centerLat: 40.97621,
  sizeMeters: 3000,   // 3km × 3km kare
};
// Google tileset'i ellipsoid radial olarak aşağı kaydır (TKGM binalar üstte kalsın)
const GOOGLE_HEIGHT_OFFSET = 0; // metre (negatif = aşağı)
// TKGM tilesetlerini yukarı kaldır (Google'ın üstünde görünsün)
const TKGM_HEIGHT_OFFSET = 8; // metre
var _google3DTileset = null;

function _applyHeightOffsetToTileset(tileset, offsetMeters) {
  if (!tileset || !offsetMeters) return;
  const center = tileset.boundingSphere?.center;
  if (!center) return;
  const normal = Cesium.Cartesian3.normalize(center, new Cesium.Cartesian3());
  const offset = Cesium.Cartesian3.multiplyByScalar(normal, offsetMeters, new Cesium.Cartesian3());
  tileset.modelMatrix = Cesium.Matrix4.fromTranslation(offset);
}

function _buildTKGMClipPlanes() {
  const { centerLon, centerLat, sizeMeters } = TKGM_CLIP_BBOX;
  const center = Cesium.Cartesian3.fromDegrees(centerLon, centerLat);
  const modelMatrix = Cesium.Transforms.eastNorthUpToFixedFrame(center);
  const half = sizeMeters / 2;
  // Union mode: dışarısı korunur, kutu içi kesilir
  const planes = [
    new Cesium.ClippingPlane(new Cesium.Cartesian3( 1, 0, 0), -half),
    new Cesium.ClippingPlane(new Cesium.Cartesian3(-1, 0, 0), -half),
    new Cesium.ClippingPlane(new Cesium.Cartesian3( 0, 1, 0), -half),
    new Cesium.ClippingPlane(new Cesium.Cartesian3( 0,-1, 0), -half),
  ];
  return new Cesium.ClippingPlaneCollection({
    planes,
    modelMatrix,
    unionClippingRegions: true,
    edgeWidth: 0,
  });
}

async function toggleGoogle3DTiles() {
  if (!viewer) return;
  if (_google3DTileset) {
    viewer.scene.primitives.remove(_google3DTileset);
    _google3DTileset = null;
    // TKGM tilesetlerini orijinal konuma geri al
    Object.values(activeLayers).forEach(t => { if (t) t.modelMatrix = Cesium.Matrix4.IDENTITY; });
    updateLayerBtn('google3d', false);
    viewer.scene.requestRender();
    return;
  }
  if (!GOOGLE_3D_TILES_API_KEY) {
    console.warn('⚠️ Google 3D Tiles için GOOGLE_MAPS_API_KEY tanımlı değil (.env)');
    alert('Google 3D Tiles katmanı için .env dosyasına GOOGLE_MAPS_API_KEY ekleyin.');
    return;
  }
  try {
    Cesium.GoogleMaps.defaultApiKey = GOOGLE_3D_TILES_API_KEY;
    _google3DTileset = await Cesium.createGooglePhotorealistic3DTileset();

    // Yükseklik offseti: TKGM bbox merkezindeki surface normal yönünde translate
    if (GOOGLE_HEIGHT_OFFSET !== 0) {
      const refPoint = Cesium.Cartesian3.fromDegrees(TKGM_CLIP_BBOX.centerLon, TKGM_CLIP_BBOX.centerLat);
      const normal = Cesium.Cartesian3.normalize(refPoint, new Cesium.Cartesian3());
      const offset = Cesium.Cartesian3.multiplyByScalar(normal, GOOGLE_HEIGHT_OFFSET, new Cesium.Cartesian3());
      _google3DTileset.modelMatrix = Cesium.Matrix4.fromTranslation(offset);
    }

    viewer.scene.primitives.add(_google3DTileset);

    // TKGM tilesetlerini yukarı kaldır → Google binalarının üstünde görünsünler
    Object.values(activeLayers).forEach(t => _applyHeightOffsetToTileset(t, TKGM_HEIGHT_OFFSET));

    // requestRenderMode aktifken yüklenen tile'lar otomatik render etmiyor
    _google3DTileset.tileLoad.addEventListener(() => viewer.scene.requestRender());
    _google3DTileset.allTilesLoaded.addEventListener(() => viewer.scene.requestRender());

    // Tileset'e uç (debug + ilk frame garantisi)
    await viewer.zoomTo(
      _google3DTileset,
      new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), 800)
    );

    updateLayerBtn('google3d', true);
    viewer.scene.requestRender();
    console.log('✅ Google 3D Tiles yüklendi', _google3DTileset);
  } catch (e) {
    console.error('[OASIS] Google 3D Tiles yüklenemedi:', e);
    alert('Google 3D Tiles yüklenemedi. API key kontrol et.');
  }
}

// ============================================================
// EK UYDU ALTLIKLARI — Clarity / Sentinel-2 EOX / TKGM Ortofoto
// ============================================================
function _toggleImageryByMatch(matchKey, btnId, providerFactory) {
  if (!viewer) return;
  const layers = viewer.imageryLayers;
  let found = null;
  for (let i = 0; i < layers.length; i++) {
    const l = layers.get(i);
    try {
      const p   = l.imageryProvider;
      const url = p._url || p.url || p._resource?.url || '';
      if (url.includes(matchKey)) { found = l; break; }
    } catch {}
  }
  if (found) {
    layers.remove(found, true);
    updateLayerBtn(btnId, false);
  } else {
    try {
      layers.add(new Cesium.ImageryLayer(providerFactory()));
      updateLayerBtn(btnId, true);
    } catch (e) {
      console.warn('[OASIS] Imagery layer eklenemedi:', e);
    }
  }
  viewer.scene.requestRender();
}

function toggleEsriClarity() {
  _toggleImageryByMatch('clarity.maptiles.arcgis.com', 'esri-clarity', () =>
    new Cesium.UrlTemplateImageryProvider({
      url: 'https://clarity.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      maximumLevel: 19,
      credit: 'Esri World Imagery (Clarity)',
    })
  );
}

function toggleHgmOrtofoto() {
  _toggleImageryByMatch('atlas.harita.gov.tr', 'hgm', () =>
    new Cesium.UrlTemplateImageryProvider({
      url: 'https://ucbp-api.tucbs.gov.tr/proxyservice/proxyservice/getByUrl?url=https://atlas.harita.gov.tr/ortofotoservis/{z}/{x}/{y}.jpg',
      maximumLevel: 19,
      credit: 'HGM Ortofoto (Harita Genel Müdürlüğü)',
    })
  );
}

// Cesium Globe — viewer.scene.globe görünürlüğü (mavi/etiketli globe)
function toggleCesiumGlobe() {
  if (!viewer) return;
  const next = !viewer.scene.globe.show;
  viewer.scene.globe.show = next;
  updateLayerBtn('esriglobe', next);
  viewer.scene.requestRender();
}

// TKGM 3B Altlık — Google MT tile servisi (lyrs=h: yol + etiket overlay)
function toggleTkgm3BAltlik() {
  _toggleImageryByMatch('mt0.google.com/vt', 'tkgm3b', () =>
    new Cesium.UrlTemplateImageryProvider({
      url: 'https://mt0.google.com/vt/lyrs=h&x={x}&y={y}&z={z}',
      maximumLevel: 20,
      credit: 'TKGM 3B Altlık',
    })
  );
}

// TKGM Terrain — quantized mesh terrain (https://terrain.tkgm.gov.tr/tilesets/tile/{z}/{x}/{y}.terrain)
async function toggleTkgmTerrain() {
  const next = _activeTerrain === 'tkgm' ? 'flat' : 'tkgm';
  await setTerrain(next);
  updateLayerBtn('tkgmterrain', next === 'tkgm');
}

function toggleSentinel() {
  _toggleImageryByMatch('tiles.maps.eox.at', 'sentinel', () =>
    new Cesium.WebMapTileServiceImageryProvider({
      url: 'https://tiles.maps.eox.at/wmts',
      layer: 's2cloudless-2023',
      style: 'default',
      format: 'image/jpeg',
      tileMatrixSetID: 'g',
      maximumLevel: 14,
      credit: 'Sentinel-2 cloudless 2023 by EOX IT Services (s2maps.eu)',
    })
  );
}

// ============================================================
// KAMERA KONTROLÜ — D-Pad Butonları + Zoom + Klavye
// Cesium'un varsayılan mouse/touch kontrolü zaten aktif:
//   Sol tık sürükle = orbit, Sağ tık sürükle = tilt/pan
//   Scroll = zoom, Orta tık = FPV döndür
// Bu panel sadece ek buton kontrolleri sağlar.
// ============================================================

const LOOK_SPEED = 0.005;    // radyan / frame
const ZOOM_SPEED = 4.0;      // metre / frame (yüksekliğe oranlanır)

// --- D-Pad (yön) butonları ---
var _lookDir = null;
var _lookRAF = null;

function startLook(dir) { _lookDir = dir; if (!_lookRAF) lookLoop(); }
function stopLook()     { _lookDir = null; }

function lookLoop() {
  if (!viewer || !_lookDir) { _lookRAF = null; return; }
  const cam = viewer.camera;
  switch (_lookDir) {
    case 'up':    cam.lookUp(LOOK_SPEED);     break;
    case 'down':  cam.lookUp(-LOOK_SPEED);    break;
    case 'left':  cam.lookRight(-LOOK_SPEED); break;
    case 'right': cam.lookRight(LOOK_SPEED);  break;
  }
  viewer.scene.requestRender();
  _lookRAF = requestAnimationFrame(lookLoop);
}

// --- Zoom butonları ---
var _zoomRAF = null;
var _zoomDir = 0;

function startZoom(dir) { _zoomDir = dir; if (!_zoomRAF) zoomLoop(); }
function stopZoom()     { _zoomDir = 0; }

function zoomLoop() {
  if (!viewer || _zoomDir === 0) { _zoomRAF = null; return; }
  // Yüksekliğe oranla hız — yüksekteyken hızlı, yakındayken yavaş
  const h = viewer.camera.positionCartographic.height || 1000;
  const spd = Math.max(h * 0.02, 1) * ZOOM_SPEED;
  if (_zoomDir > 0) viewer.camera.moveForward(spd);
  else              viewer.camera.moveBackward(spd);
  viewer.scene.requestRender();
  _zoomRAF = requestAnimationFrame(zoomLoop);
}

// --- Klavye ok tuşları ---
var _keyDirs = new Set();
var _keyRAF  = null;

function keyLoop() {
  if (!viewer || !_keyDirs.size) { _keyRAF = null; return; }
  const cam = viewer.camera;
  if (_keyDirs.has('ArrowLeft'))  cam.lookRight(-LOOK_SPEED);
  if (_keyDirs.has('ArrowRight')) cam.lookRight(LOOK_SPEED);
  if (_keyDirs.has('ArrowUp'))    cam.lookUp(LOOK_SPEED);
  if (_keyDirs.has('ArrowDown'))  cam.lookUp(-LOOK_SPEED);
  viewer.scene.requestRender();
  _keyRAF = requestAnimationFrame(keyLoop);
}

document.addEventListener('keydown', e => {
  if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) {
    e.preventDefault();
    _keyDirs.add(e.key);
    if (!_keyRAF) keyLoop();
  }
});
document.addEventListener('keyup', e => {
  _keyDirs.delete(e.key);
});

console.log('✅ TKGM 3B CBS script yüklendi');

// ============================================================
// OASIS AFET PANEL\u0130 — Dinamik API Entegrasyonu
// ============================================================

// Aynı origin: nginx /api/* isteklerini backend'e yönlendirir. Gerekirse config ile ezilebilir.
const OASIS_API = OASIS_CONFIG.apiBaseUrl || '';

var _disasterEntities  = [];   // Haritadaki report pin entity'leri
var _clusterEntities   = [];   // Haritadaki cluster pin entity'leri
var _activeTab         = 'reports';
var _refreshTimer      = null;
var _allReports        = [];
var _allClusters       = [];

// Akyazı bina alanı bbox (akyazi_buildings.geojson extent + ~500m pad)
// Performans için: bu kutu dışındaki rapor/küme verisi UI'a alınmaz.
const AKYAZI_BBOX = { minLon: 37.895, maxLon: 37.940, minLat: 40.970, maxLat: 40.990 };
function _inAkyaziBBox(lat, lon) {
  const la = parseFloat(lat), lo = parseFloat(lon);
  if (isNaN(la) || isNaN(lo)) return false;
  return lo >= AKYAZI_BBOX.minLon && lo <= AKYAZI_BBOX.maxLon
      && la >= AKYAZI_BBOX.minLat && la <= AKYAZI_BBOX.maxLat;
}
var _lastReportsCount  = 0;
var _lastRefreshAt     = null;
var _showUnprocessedOnly = false;

const AUTO_REFRESH_MS = 20000;
let _lastReportsHash = '';
let _lastClustersHash = '';

// ---- Sel Simülasyonu ----
var _floodEntities     = [];   // Su yüzeyi entity'leri
var _floodEnabled      = false;
var _floodWaterHeight  = 0;    // Slider değeri (0–5 metre)

// ---- Seviye Filtreleri ----
// Pin filtresi: 3 seviye (kırmızı/turuncu/mavi)
const PIN_LEVELS = ['high', 'medium', 'low_medium'];
var _pinLevelFilter = new Set(PIN_LEVELS);
// Bina filtresi: 6 seviye (tam NAPSG skalası)
const SEVERITY_LEVELS = ['extreme', 'high', 'medium_high', 'medium', 'low_medium', 'low'];
var _buildingLevelFilter = new Set(SEVERITY_LEVELS);

// ---- Tarihsel Filtre ----
var _timeFilterHours = 0;    // 0 = tümü, >0 = son N saat
var _timeFilterFrom  = null; // Date veya null
var _timeFilterTo    = null; // Date veya null

function _getReportDate(r) {
  const d = r.report_date || r.created_at || r.image_date;
  return d ? new Date(d) : null;
}

function _passesTimeFilter(r) {
  const d = _getReportDate(r);
  if (!d || isNaN(d)) return _timeFilterHours === 0 && !_timeFilterFrom;
  if (_timeFilterHours > 0) {
    const cutoff = new Date(Date.now() - _timeFilterHours * 3600000);
    return d >= cutoff;
  }
  if (_timeFilterFrom || _timeFilterTo) {
    if (_timeFilterFrom && d < _timeFilterFrom) return false;
    if (_timeFilterTo) {
      const end = new Date(_timeFilterTo);
      end.setHours(23, 59, 59, 999);
      if (d > end) return false;
    }
    return true;
  }
  return true; // tümü
}

var _eventTypeFilter   = null; // null = tümü, string = sadece o tip
var _clusterTypeFilter = null;

function getFilteredReports() {
  return _allReports.filter(r => {
    if (_showUnprocessedOnly && !isUnprocessedReport(r)) return false;
    if (!_pinLevelFilter.has(getPinLevel(r.severity_score ?? null))) return false;
    if (!_passesTimeFilter(r)) return false;
    if (_eventTypeFilter) {
      const t = (r.processed_disaster_type || r.event_define || 'Bilinmiyor').trim();
      if (t !== _eventTypeFilter) return false;
    }
    return true;
  });
}

function getFilteredClusters() {
  return _allClusters.filter(c => {
    if (_clusterTypeFilter) {
      const t = (c.event_type || 'Bilinmiyor').trim();
      if (t !== _clusterTypeFilter) return false;
    }
    return true;
  });
}

function setEventTypeFilter(type) {
  if (type === null) _eventTypeFilter = null;
  else _eventTypeFilter = (_eventTypeFilter === type) ? null : type;
  _applyTimeFilter();
  if (typeof updateRightSidebar === 'function') {
    try { updateRightSidebar(); } catch {}
  }
}

function setClusterTypeFilter(type) {
  if (type === null) _clusterTypeFilter = null;
  else _clusterTypeFilter = (_clusterTypeFilter === type) ? null : type;
  addClusterPins(getFilteredClusters());
  if (typeof updateRightSidebar === 'function') {
    try { updateRightSidebar(); } catch {}
  }
  if (viewer) viewer.scene.requestRender();
}

function isUnprocessedReport(report) {
  const hasProcessedStatus = !!(report.processed_user_status && String(report.processed_user_status).trim());
  const hasSeverity = report.severity_score !== null && report.severity_score !== undefined;
  const hasProcessedType = !!(report.processed_disaster_type && String(report.processed_disaster_type).trim());
  return !(hasProcessedStatus || hasSeverity || hasProcessedType);
}

function setTimePreset(hours) {
  _timeFilterHours = hours;
  _timeFilterFrom = null;
  _timeFilterTo = null;
  // Input'ları temizle
  const fromEl = document.getElementById('tfDateFrom');
  const toEl   = document.getElementById('tfDateTo');
  if (fromEl) fromEl.value = '';
  if (toEl)   toEl.value = '';
  // Aktif butonu işaretle
  document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.hours) === hours);
  });
  _applyTimeFilter();
}

function applyCustomDateRange() {
  const fromEl = document.getElementById('tfDateFrom');
  const toEl   = document.getElementById('tfDateTo');
  const fromVal = fromEl ? fromEl.value : '';
  const toVal   = toEl   ? toEl.value   : '';

  _timeFilterHours = 0;
  _timeFilterFrom = fromVal ? new Date(fromVal) : null;
  _timeFilterTo   = toVal   ? new Date(toVal)   : null;

  // Preset butonlarını deaktif et
  document.querySelectorAll('.tf-btn').forEach(btn => btn.classList.remove('active'));
  if (!fromVal && !toVal) {
    document.querySelector('.tf-btn[data-hours="0"]')?.classList.add('active');
  }
  _applyTimeFilter();
}

function _applyTimeFilter() {
  const filtered = getFilteredReports();
  addReportPins(filtered);
  applyBuildingUrgencyZones(filtered);
  addClusterPins(getFilteredClusters());
  renderList();
  updateLegendCounts();
  _updateTimeFilterInfo();
}

function _updateTimeFilterInfo() {
  const infoEl = document.getElementById('tfInfo');
  const freshEl = document.getElementById('tfFreshness');
  if (!infoEl) return;

  const filtered = getFilteredReports();
  const total = _allReports.length;

  if (_timeFilterHours > 0) {
    const label = _timeFilterHours < 24 ? `Son ${_timeFilterHours} saat` :
                  _timeFilterHours < 168 ? `Son ${Math.round(_timeFilterHours/24)} gün` :
                  `Son ${Math.round(_timeFilterHours/24)} gün`;
    infoEl.innerHTML = `${label}: <strong>${filtered.length}</strong> / ${total}`;
  } else if (_timeFilterFrom || _timeFilterTo) {
    const f = _timeFilterFrom ? _timeFilterFrom.toLocaleDateString('tr-TR') : '∞';
    const t = _timeFilterTo   ? _timeFilterTo.toLocaleDateString('tr-TR')   : '∞';
    infoEl.innerHTML = `${f} — ${t}: <strong>${filtered.length}</strong> / ${total}`;
  } else {
    infoEl.innerHTML = `Tüm tarihler: <strong>${filtered.length}</strong> / ${total}`;
  }

  // Freshness çubukları (son 1h / 6h / 24h / 3d / 7d)
  if (!freshEl) return;
  const now = Date.now();
  const buckets = [
    { label: '1s', hours: 1, color: '#22c55e' },
    { label: '6s', hours: 6, color: '#84cc16' },
    { label: '24s', hours: 24, color: '#eab308' },
    { label: '3g', hours: 72, color: '#f97316' },
    { label: '7g', hours: 168, color: '#ef4444' },
    { label: '7g+', hours: Infinity, color: '#6b7280' },
  ];
  const counts = buckets.map(() => 0);
  _allReports.forEach(r => {
    const d = _getReportDate(r);
    if (!d) { counts[counts.length - 1]++; return; }
    const ageH = (now - d.getTime()) / 3600000;
    for (let i = 0; i < buckets.length; i++) {
      if (ageH <= buckets[i].hours || i === buckets.length - 1) { counts[i]++; break; }
    }
  });
  const max = Math.max(...counts, 1);
  freshEl.innerHTML = buckets.map((b, i) =>
    `<div style="flex:1;text-align:center" title="${b.label}: ${counts[i]} rapor">
      <div style="height:${Math.max(Math.round(counts[i]/max*20), 2)}px;background:${b.color};border-radius:2px;margin:0 1px"></div>
      <div style="font-size:7px;color:var(--td);margin-top:2px">${b.label}</div>
    </div>`
  ).join('');
}

// ---- Lejant Filtre Toggle'ları ----
function togglePinLevel(level) {
  if (_pinLevelFilter.has(level)) _pinLevelFilter.delete(level);
  else _pinLevelFilter.add(level);
  const el = document.getElementById('lp-pin-' + level);
  if (el) el.classList.toggle('lp-checked', _pinLevelFilter.has(level));
  const filtered = getFilteredReports();
  addReportPins(filtered);
  renderList();
}

function toggleBuildingLevel(level) {
  if (_buildingLevelFilter.has(level)) _buildingLevelFilter.delete(level);
  else _buildingLevelFilter.add(level);
  const el = document.getElementById('lp-bld-' + level);
  if (el) el.classList.toggle('lp-checked', _buildingLevelFilter.has(level));
  applyBuildingColoring(getFilteredReports());
}

function setAllPinLevels(checked) {
  PIN_LEVELS.forEach(l => {
    if (checked) _pinLevelFilter.add(l); else _pinLevelFilter.delete(l);
    const el = document.getElementById('lp-pin-' + l);
    if (el) el.classList.toggle('lp-checked', checked);
  });
  const filtered = getFilteredReports();
  addReportPins(filtered);
  renderList();
}

function setAllBuildingLevels(checked) {
  SEVERITY_LEVELS.forEach(l => {
    if (checked) _buildingLevelFilter.add(l); else _buildingLevelFilter.delete(l);
    const el = document.getElementById('lp-bld-' + l);
    if (el) el.classList.toggle('lp-checked', checked);
  });
  applyBuildingColoring(getFilteredReports());
}

function toggleLegendPanel() {
  const panel = document.getElementById('legendPanel');
  if (panel) panel.classList.toggle('lp-open');
}

function updateLegendCounts() {
  PIN_LEVELS.forEach(level => {
    const cnt = _allReports.filter(r => getPinLevel(r.severity_score ?? null) === level).length;
    const el = document.getElementById('lp-pin-cnt-' + level);
    if (el) el.textContent = cnt;
  });
}

// ---- Accordion panel aç/kapat ----
function toggleSFPanel(panelId) {
  const panel = document.getElementById(panelId);
  if (panel) panel.classList.toggle('sf-panel-open');
}

// Kümeler için tarihsel filtre
function _passesTimeFilterCluster(c) {
  const raw = c.created_at || c.first_report_at || c.report_date;
  const d = raw ? new Date(raw) : null;
  if (!d || isNaN(d)) return _timeFilterHours === 0 && !_timeFilterFrom;
  if (_timeFilterHours > 0) {
    const cutoff = new Date(Date.now() - _timeFilterHours * 3600000);
    return d >= cutoff;
  }
  if (_timeFilterFrom || _timeFilterTo) {
    if (_timeFilterFrom && d < _timeFilterFrom) return false;
    if (_timeFilterTo) {
      const end = new Date(_timeFilterTo);
      end.setHours(23, 59, 59, 999);
      if (d > end) return false;
    }
    return true;
  }
  return true;
}


// ---- NAPSG / ISO 22324 Severity Renk Paleti (bina renklendirme — 6 seviye) ----
const SEVERITY_COLOR = {
  extreme:     { hex: '#ED1AFC', alpha: 0.65, label: 'AŞIRI TEHLİKE'   }, // score ≥ 9
  high:        { hex: '#FF181E', alpha: 0.60, label: 'YÜKSEK TEHLİKE'  }, // score ≥ 7
  medium_high: { hex: '#FF8918', alpha: 0.55, label: 'TEHLİKE İZLEME'  }, // score ≥ 5
  medium:      { hex: '#FFD718', alpha: 0.50, label: 'ORTA RİSK'        }, // score ≥ 3
  low_medium:  { hex: '#237ACF', alpha: 0.45, label: 'DÜŞÜK-ORTA RİSK' }, // score ≥ 1
  low:         { hex: '#00AC3A', alpha: 0.40, label: 'DÜŞÜK RİSK'       }, // score ≥ 0
};
const SEVERITY_ORDER = { extreme: 5, high: 4, medium_high: 3, medium: 2, low_medium: 1, low: 0 };

// 6 seviye — bina renklendirme için
function getSeverityLevel(score) {
  if (score === null || score === undefined) return null;
  if (score >= 9) return 'extreme';
  if (score >= 7) return 'high';
  if (score >= 5) return 'medium_high';
  if (score >= 3) return 'medium';
  if (score >= 1) return 'low_medium';
  return 'low';
}

// 3 seviye — pin filtreleme ve ikonlar için
function getPinLevel(score) {
  if (score === null || score === undefined) return 'low_medium';
  if (score >= 7) return 'high';
  if (score >= 3) return 'medium';
  return 'low_medium';
}


// ---- Cluster event renk paleti ----
const CLUSTER_EVENT_COLOR = {
  'sel':              '#3b82f6',   // mavi
  'deprem':           '#ef4444',   // kırmızı
  'yangin':           '#f97316',   // turuncu
  'heyelan':          '#a16207',   // kahverengi
  'elektrik':         '#facc15',   // sarı
  'yol':              '#94a3b8',   // gri
  'teknik':           '#a78bfa',   // mor
  'firtina':          '#67e8f9',   // açık mavi
  'kar':              '#e0f2fe',   // beyaz-mavi
  'default':          '#5998C5',
};

function getClusterColor(eventType) {
  if (!eventType) return CLUSTER_EVENT_COLOR.default;
  const t = eventType.toLowerCase();
  for (const [k, v] of Object.entries(CLUSTER_EVENT_COLOR)) {
    if (k !== 'default' && t.includes(k)) return v;
  }
  return CLUSTER_EVENT_COLOR.default;
}

// ---- Eski EVENT_COLORS (backward compat) ----
const EVENT_COLORS = {
  sel:        { bg: '#3b82f6', text: '#fff', label: 'SEL' },
  deprem:     { bg: '#ef4444', text: '#fff', label: 'DEPREM' },
  yangin:     { bg: '#f97316', text: '#fff', label: 'YANGIN' },
  heyelan:    { bg: '#a16207', text: '#fff', label: 'HEYELAN' },
  firtina:    { bg: '#67e8f9', text: '#000', label: 'FIRTINA' },
  kar:        { bg: '#e0f2fe', text: '#000', label: 'KAR' },
  default:    { bg: '#5998C5', text: '#fff', label: 'AFET' },
};

function getEventColor(type) {
  if (!type) return EVENT_COLORS.default;
  const t = type.toLowerCase();
  for (const [k, v] of Object.entries(EVENT_COLORS)) {
    if (t.includes(k)) return v;
  }
  return EVENT_COLORS.default;
}

// ============================================================
// BİNA RENKLENDİRME — Gerçek GeoJSON Ayak İzleri + NAPSG Paleti
// data/akyazi_buildings.geojson → centroid tabanlı yakın bina tespiti
// Bulunan binalar severity_score'a göre NAPSG rengiyle ClassificationPrimitive
// ============================================================

var _buildingZones     = [];
var _buildingCentroids = null;

async function loadBuildingData() {
  if (_buildingCentroids) return;
  try {
    const resp = await fetch('data/akyazi_buildings.geojson');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const geojson = await resp.json();
    _buildingCentroids = geojson.features.map(f => {
      // MultiPolygon: coordinates[polygonIdx][ringIdx][pointIdx] = [lon, lat]
      const ring = f.geometry.coordinates[0][0];
      const n = ring.length;
      const lon = ring.reduce((s, c) => s + c[0], 0) / n;
      const lat = ring.reduce((s, c) => s + c[1], 0) / n;
      return { lon, lat, feature: f };
    });
    console.log(`[OASIS] ${_buildingCentroids.length} Akyazı binası yüklendi`);
  } catch (e) {
    console.warn('[OASIS] Bina verisi yüklenemedi:', e.message);
  }
}

function _findBuildingsNear(lat, lon, radiusM) {
  if (!_buildingCentroids) return [];
  const R = 6378137;
  const cosLat = Math.cos(lat * Math.PI / 180);
  return _buildingCentroids.filter(b => {
    const dlat = (b.lat - lat) * R * Math.PI / 180;
    const dlon = (b.lon - lon) * R * cosLat * Math.PI / 180;
    return (dlat * dlat + dlon * dlon) <= radiusM * radiusM;
  });
}

var _buildingsVisible = true;

const _BUILDING_TILESET_KEYS = ['texture', 'solid', 'mimari', 'section'];

function toggleBuildingLayer() {
  _buildingsVisible = !_buildingsVisible;
  updateLayerBtn('buildings', _buildingsVisible);

  // 3D bina tileset'lerinin görünürlüğünü değiştir (texture/solid/mimari/section)
  _BUILDING_TILESET_KEYS.forEach(k => {
    const ts = activeLayers && activeLayers[k];
    if (ts) { try { ts.show = _buildingsVisible; } catch (e) {} }
  });

  // GeoJSON aciliyet renklendirme overlay
  if (_buildingsVisible) {
    applyBuildingColoring(getFilteredReports());
  } else {
    clearBuildingZones();
  }
  if (viewer) viewer.scene.requestRender();
}

// Cache: building feature id → Cartesian3[][] (her MultiPolygon halkası ayrı array)
var _buildingPositionsCache = new Map();

function _getCachedBuildingPositions(feature) {
  const id = feature.properties.id;
  let cached = _buildingPositionsCache.get(id);
  if (cached) return cached;
  cached = feature.geometry.coordinates.map(polygon => {
    try {
      return Cesium.Cartesian3.fromDegreesArray(polygon[0].flat());
    } catch { return null; }
  }).filter(Boolean);
  _buildingPositionsCache.set(id, cached);
  return cached;
}

function applyBuildingColoring(reports) {
  clearBuildingZones();
  if (!_buildingsVisible) { if (viewer) viewer.scene.requestRender(); return; }
  if (!viewer || !_buildingCentroids || !reports || !reports.length) return;

  // buildingId → en yüksek severity seviyesi
  const colorMap = new Map(); // id → { level, feature }
  reports.forEach(r => {
    const lat = parseFloat(r.latitude);
    const lon = parseFloat(r.longitude);
    if (isNaN(lat) || isNaN(lon)) return;
    const level = getSeverityLevel(r.severity_score ?? null);
    if (!level) return;

    let nearby = _findBuildingsNear(lat, lon, 30);
    if (nearby.length === 0) nearby = _findBuildingsNear(lat, lon, 80).slice(0, 1);

    nearby.forEach(b => {
      const id = b.feature.properties.id;
      const existing = colorMap.get(id);
      if (!existing || SEVERITY_ORDER[level] > SEVERITY_ORDER[existing.level]) {
        colorMap.set(id, { level, feature: b.feature });
      }
    });
  });

  // Severity seviyesine göre grupla → tek ClassificationPrimitive per renk
  const groups = {};
  colorMap.forEach(({ level, feature }) => {
    (groups[level] ??= []).push(feature);
  });

  let cacheHits = 0, cacheMisses = 0;
  Object.entries(groups).forEach(([level, features]) => {
    if (!_buildingLevelFilter.has(level)) return;
    const col = SEVERITY_COLOR[level];
    const cesiumColor = Cesium.Color.fromCssColorString(col.hex).withAlpha(col.alpha);
    const colorAttr = Cesium.ColorGeometryInstanceAttribute.fromColor(cesiumColor);

    const instances = features.flatMap(f => {
      const hadCache = _buildingPositionsCache.has(f.properties.id);
      const positionsList = _getCachedBuildingPositions(f);
      if (hadCache) cacheHits++; else cacheMisses++;
      return positionsList.map(positions => new Cesium.GeometryInstance({
        geometry: new Cesium.PolygonGeometry({
          polygonHierarchy: new Cesium.PolygonHierarchy(positions),
          height: 0,
          extrudedHeight: 200,
        }),
        attributes: { color: colorAttr },
      }));
    });
    if (!instances.length) return;

    try {
      const prim = viewer.scene.primitives.add(
        new Cesium.ClassificationPrimitive({
          geometryInstances: instances,
          classificationType: Cesium.ClassificationType.CESIUM_3D_TILE,
          releaseGeometryInstances: false,
        })
      );
      _buildingZones.push(prim);
    } catch (e) {
      console.warn('[OASIS] ClassificationPrimitive hatası:', e);
    }
  });

  viewer.scene.requestRender();
  console.log(`[OASIS] ${colorMap.size} bina renklendi (${reports.length} rapor) | cache: ${cacheHits} hit, ${cacheMisses} miss, ${_buildingPositionsCache.size} toplam`);
}

// applyBuildingUrgencyZones → applyBuildingColoring alias (çağıran kodlar güncellenmeden çalışsın)
function applyBuildingUrgencyZones(reports) {
  applyBuildingColoring(reports);
  if (_roadLayerEnabled) renderRoadLayer(reports);
}

// ============================================================
// ORDU YOL AĞI KATMANI
// Yol verisi: data/ordu-ili-yol-ai.geojson (MultiLineString)
// Yeşil = açık, Turuncu = etkilenmiş, Kırmızı = kapalı
// Durum; yakın raporların severity_score + active_indicators
// değerlerine göre deterministik olarak hesaplanır.
// ============================================================

var _roadFeatures   = null;   // yüklenen GeoJSON features
var _roadPrimitives = [];     // GroundPolylinePrimitive listesi (temizleme)
var _roadLayerEnabled = false;
var _roadStatusFilter = null; // null|'closed'|'warning'|'open'
var _lastRoadSig    = null;

// Bir raporun yakın yolları etkilediği maksimum mesafe (metre)
const ROAD_AFFECT_RADIUS_M = 150;

// Harita üzerindeki görsel stil: durum → renk/genişlik
const ROAD_STATUS_STYLE = {
  open:    { hex: '#22c55e', alpha: 0.80, width: 2.5 }, // yeşil
  warning: { hex: '#f97316', alpha: 0.95, width: 4.0 }, // turuncu
  closed:  { hex: '#ef4444', alpha: 1.00, width: 5.5 }, // kırmızı
};

// Yaya/bisiklet yolları yol ağı analizine dahil edilmez
const _SKIP_ROAD_TYPES = new Set(['footway', 'path', 'steps', 'cycleway', 'bridleway', 'pedestrian']);

function _getBuildingBBox(padFactor = 0.05, padMin = 0.002) {
  if (!_buildingCentroids || !_buildingCentroids.length) return null;
  let minLon =  Infinity, maxLon = -Infinity, minLat =  Infinity, maxLat = -Infinity;
  _buildingCentroids.forEach(b => {
    if (b.lon < minLon) minLon = b.lon;
    if (b.lon > maxLon) maxLon = b.lon;
    if (b.lat < minLat) minLat = b.lat;
    if (b.lat > maxLat) maxLat = b.lat;
  });
  const padLon = (maxLon - minLon) * padFactor + padMin;
  const padLat = (maxLat - minLat) * padFactor + padMin;
  return {
    minLon: minLon - padLon, maxLon: maxLon + padLon,
    minLat: minLat - padLat, maxLat: maxLat + padLat,
  };
}

function _featureIntersectsBBox(f, bb) {
  for (const line of f.geometry.coordinates) {
    for (const p of line) {
      const lon = p[0], lat = p[1];
      if (lon >= bb.minLon && lon <= bb.maxLon && lat >= bb.minLat && lat <= bb.maxLat) return true;
    }
  }
  return false;
}

async function loadRoadData() {
  if (_roadFeatures !== null) return;
  try {
    if (!_buildingCentroids) await loadBuildingData();
    const resp = await fetch('data/ordu-ili-yol-ai.geojson');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const geojson = await resp.json();
    const bbox = _getBuildingBBox();
    _roadFeatures = geojson.features.filter(f => {
      const tip = (f.properties['TİPİ'] || '').toLowerCase();
      if (_SKIP_ROAD_TYPES.has(tip)) return false;
      if (!f.geometry || !f.geometry.coordinates) return false;
      if (!bbox) return true;
      return _featureIntersectsBBox(f, bbox);
    });
    console.log(`[OASIS] ${_roadFeatures.length} yol segmenti yüklendi (Akyazı bbox sınırlı)`);
  } catch (e) {
    console.warn('[OASIS] Yol verisi yüklenemedi:', e.message);
    _roadFeatures = [];
  }
}

function toggleReportPinsLayer() {
  _reportPinsVisible = !_reportPinsVisible;
  const tgl = document.getElementById('tgl-event-types');
  if (tgl) tgl.classList.toggle('on', _reportPinsVisible);
  addReportPins(getFilteredReports());
}

function toggleClusterPinsLayer() {
  _clusterPinsVisible = !_clusterPinsVisible;
  const tgl = document.getElementById('tgl-cluster-types');
  if (tgl) tgl.classList.toggle('on', _clusterPinsVisible);
  addClusterPins(getFilteredClusters());
}

async function toggleRoadLayer() {
  _roadLayerEnabled = !_roadLayerEnabled;
  updateLayerBtn('roads', _roadLayerEnabled);
  const tgl = document.getElementById('tgl-road-layer');
  if (tgl) tgl.classList.toggle('on', _roadLayerEnabled);
  const panel = document.getElementById('road-status-panel');
  if (panel) panel.style.display = _roadLayerEnabled ? 'block' : 'none';
  if (_roadLayerEnabled) {
    await loadRoadData();
    renderRoadLayer(getFilteredReports());
  } else {
    clearRoadLayer();
    _updateRoadStats(0, 0, 0);
    _lastRoadSig = null;
  }
  if (viewer) viewer.scene.requestRender();
}

function clearRoadLayer() {
  _roadPrimitives.forEach(p => {
    try { viewer.scene.groundPrimitives.remove(p); } catch {}
  });
  _roadPrimitives = [];
  _lastRoadSig = null;
}

// ── Nokta → çizgi segmenti mesafesi (metre, düz dünya yaklaşımı) ──
function _distPointToSegmentM(pLon, pLat, aLon, aLat, bLon, bLat) {
  const cosLat  = Math.cos(pLat * Math.PI / 180);
  const mPerLon = 111320 * cosLat;
  const mPerLat = 111320;

  const px = pLon * mPerLon, py = pLat * mPerLat;
  const ax = aLon * mPerLon, ay = aLat * mPerLat;
  const bx = bLon * mPerLon, by = bLat * mPerLat;

  const dx = bx - ax, dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  const t = lenSq === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
  const nx = ax + t * dx, ny = ay + t * dy;
  return Math.sqrt((px - nx) ** 2 + (py - ny) ** 2);
}

// ── Bir rapor ile bir GeoJSON feature arasındaki minimum mesafe (metre) ──
function _minDistReportToFeature(rLon, rLat, feature) {
  // Bounding box ön filtresi
  const pad = ROAD_AFFECT_RADIUS_M / 111320;
  let minD = Infinity;

  for (const line of feature.geometry.coordinates) {
    for (let i = 0; i < line.length - 1; i++) {
      const [aLon, aLat] = line[i];
      const [bLon, bLat] = line[i + 1];

      // Bounding box hızlı eleme (segment)
      if (
        rLon < Math.min(aLon, bLon) - pad || rLon > Math.max(aLon, bLon) + pad ||
        rLat < Math.min(aLat, bLat) - pad || rLat > Math.max(aLat, bLat) + pad
      ) continue;

      const d = _distPointToSegmentM(rLon, rLat, aLon, aLat, bLon, bLat);
      if (d < minD) minD = d;
      if (minD < 1) return minD; // yeterince yakın, erken çık
    }
  }
  return minD;
}

// ── Bir rapordan yol durumunu türet ──
function _statusFromReport(r) {
  const sev    = r.severity_score ?? 0;
  const active = r.active_indicators || [];
  const hasRoadBlock = active.includes('road_blocked') || active.includes('area_isolated');
  const hasLandslide = active.includes('landslide_active');
  const hasFlood     = active.includes('flood_water_rising');
  const hasBuildFail = active.includes('building_collapsed');
  const hasInfra     = active.includes('utility_disrupted') || active.includes('utility_dangerous');

  if (sev >= 7 && (hasRoadBlock || hasLandslide || hasBuildFail)) return 'closed';
  if (sev >= 5 && hasRoadBlock)                                   return 'closed';
  if (sev >= 7 && hasFlood)                                       return 'closed';
  if (sev >= 5 || hasRoadBlock || hasLandslide)                   return 'warning';
  if (sev >= 3 && (hasFlood || hasBuildFail || hasInfra))         return 'warning';
  return 'open';
}

// ── Tüm raporları en yakın feature'a ata, feature başına max status hesapla ──
// reports → Map<featureIdx, status>
function _computeRoadStatuses(reports) {
  const ORDER = { open: 0, warning: 1, closed: 2 };
  const result = new Map();
  if (!reports || !reports.length) return result;

  for (const r of reports) {
    const rLon = parseFloat(r.longitude);
    const rLat = parseFloat(r.latitude);
    if (isNaN(rLon) || isNaN(rLat)) continue;

    const status = _statusFromReport(r);
    if (status === 'open') continue; // open sadece varsayılan

    // En yakın feature'ı bul
    let bestIdx = -1, bestDist = Infinity;
    for (let i = 0; i < _roadFeatures.length; i++) {
      const d = _minDistReportToFeature(rLon, rLat, _roadFeatures[i]);
      if (d < bestDist) {
        bestDist = d;
        bestIdx = i;
        if (d < 1) break;
      }
    }
    if (bestIdx < 0 || bestDist > ROAD_AFFECT_RADIUS_M) continue;

    const prev = result.get(bestIdx) || 'open';
    if (ORDER[status] > ORDER[prev]) result.set(bestIdx, status);
  }
  return result;
}

function _updateRoadStats(closedCount, warningCount, openCount) {
  const total = closedCount + warningCount + openCount;
  const c = document.getElementById('road-closed-count');
  const w = document.getElementById('road-warning-count');
  const o = document.getElementById('road-open-count');
  if (c) c.textContent = closedCount;
  if (w) w.textContent = warningCount;
  if (o) o.textContent = openCount;
  const ra = document.getElementById('rs-road-all-val');
  const rc = document.getElementById('rs-road-closed-val');
  const rw = document.getElementById('rs-road-warning-val');
  const ro = document.getElementById('rs-road-open-val');
  if (ra) ra.textContent = total;
  if (rc) rc.textContent = closedCount;
  if (rw) rw.textContent = warningCount;
  if (ro) ro.textContent = openCount;
  _refreshRoadFilterUi();
}

function _refreshRoadFilterUi() {
  ['closed', 'warning', 'open'].forEach(s => {
    const el = document.getElementById('rs-road-' + s);
    if (el) el.classList.toggle('rs-active', _roadStatusFilter === s);
  });
  const elAll = document.getElementById('rs-road-all');
  if (elAll) elAll.classList.toggle('rs-active', _roadStatusFilter === null);
}

async function toggleRoadStatusFilter(status) {
  _roadStatusFilter = (_roadStatusFilter === status) ? null : status;
  if (!_roadLayerEnabled) {
    await toggleRoadLayer();
  } else {
    renderRoadLayer(getFilteredReports());
  }
  _refreshRoadFilterUi();
}

async function setRoadFilterAll() {
  _roadStatusFilter = null;
  if (!_roadLayerEnabled) {
    await toggleRoadLayer();
  } else {
    _lastRoadSig = null; // re-render zorla
    renderRoadLayer(getFilteredReports());
  }
  _refreshRoadFilterUi();
}

function _reportsRoadSig(reports) {
  if (!reports || !reports.length) return '';
  return reports.map(r => (r.id || r.report_id || '') + ':' + (r.severity_score ?? '')).sort().join('|');
}

function renderRoadLayer(reports) {
  if (!viewer || !_roadLayerEnabled || !_roadFeatures || !_roadFeatures.length) return;

  const sig = _reportsRoadSig(reports) + '||' + (_roadStatusFilter || 'all');
  if (sig === _lastRoadSig && _roadPrimitives.length) return;
  _lastRoadSig = sig;

  // En yakın yol eşleme: sadece raporun en yakın olduğu feature etkilenir
  const statusMap = _computeRoadStatuses(reports);
  const groups = { open: [], warning: [], closed: [] };
  for (let i = 0; i < _roadFeatures.length; i++) {
    const status = statusMap.get(i) || 'open';
    groups[status].push(_roadFeatures[i]);
  }

  // Cross-fade: önce yeni primitive ekle, eskisini sonra sil (yanıp sönmeyi önler)
  const oldPrims = _roadPrimitives;
  _roadPrimitives = [];

  for (const [status, features] of Object.entries(groups)) {
    if (!features.length) continue;
    if (_roadStatusFilter && status !== _roadStatusFilter) continue;
    const s = ROAD_STATUS_STYLE[status];
    const col = Cesium.Color.fromCssColorString(s.hex).withAlpha(s.alpha);

    const instances = [];
    for (const feature of features) {
      for (const line of feature.geometry.coordinates) {
        if (line.length < 2) continue;
        try {
          instances.push(new Cesium.GeometryInstance({
            geometry: new Cesium.GroundPolylineGeometry({
              positions: Cesium.Cartesian3.fromDegreesArray(line.flat()),
              width: s.width,
            }),
            attributes: {
              color: Cesium.ColorGeometryInstanceAttribute.fromColor(col),
            },
          }));
        } catch {}
      }
    }
    if (!instances.length) continue;

    try {
      const prim = viewer.scene.groundPrimitives.add(
        new Cesium.GroundPolylinePrimitive({
          geometryInstances: instances,
          appearance: new Cesium.PolylineColorAppearance(),
        })
      );
      _roadPrimitives.push(prim);
    } catch (e) {
      console.warn('[OASIS] Yol primitive hatası:', e.message);
    }
  }

  if (oldPrims.length) {
    setTimeout(() => {
      oldPrims.forEach(p => { try { viewer.scene.groundPrimitives.remove(p); } catch {} });
      if (viewer) viewer.scene.requestRender();
    }, 500);
  }

  _updateRoadStats(groups.closed.length, groups.warning.length, groups.open.length);
  viewer.scene.requestRender();
  console.log(`[OASIS] Yol katmanı: ${groups.open.length} açık | ${groups.warning.length} etkilenmiş | ${groups.closed.length} kapalı`);
}

function clearBuildingZones() {
  _buildingZones.forEach(p => {
    try { viewer.scene.primitives.remove(p); } catch {}
    try { viewer.scene.groundPrimitives.remove(p); } catch {}
  });
  _buildingZones = [];
  _resetTilesetStyles();
}

// ── TKGM TILESET GLOBAL RENK STİLİ ── (opsiyonel ek destek)
function _applyTilesetUrgencyStyle() {
  // ClassificationPrimitive zaten tile'ları boyuyor,
  // ek global stil uygulama → kapatıyoruz
}

function _styleOneTileset(tileset) {
  // ClassificationPrimitive kullanıyoruz, global stil gereksiz
}

function _resetTilesetStyles() {
  const defaultStyles = {
    solid:   "color('#c8a97e', 1.0)",
    mimari:  "color('#a8c8e8', 0.9)",
    texture: null,
    section: "color('#ffffff', 1.0)",
  };
  Object.entries(defaultStyles).forEach(([key, expr]) => {
    const ts = activeLayers[key];
    if (!ts) return;
    if (expr) {
      ts.style = new Cesium.Cesium3DTileStyle({ color: expr });
    } else {
      ts.style = undefined;
    }
  });
}

function _resetOneTilesetStyle(tileset) {
  const entry = Object.entries(activeLayers).find(([, v]) => v === tileset);
  if (!entry) return;
  const key = entry[0];
  const defaults = {
    solid:   "color('#c8a97e', 1.0)",
    mimari:  "color('#a8c8e8', 0.9)",
    section: "color('#ffffff', 1.0)",
  };
  if (key === 'texture') {
    tileset.style = undefined;
  } else if (defaults[key]) {
    tileset.style = new Cesium.Cesium3DTileStyle({ color: defaults[key] });
  }
}

function getSevClass(sev) {
  if (!sev) return 'sev-none';
  if (sev >= 7)  return 'sev-high';
  if (sev >= 4)  return 'sev-mid';
  return 'sev-low';
}

// ---- NAPSG Pin İkonları (afet türü + severity seviyesi) ----
// İkon tier: high (extreme/high), mid (medium_high/medium), low (low_medium/low/unknown)
const DISASTER_ICONS = {
  sel:     { high: 'icons/Flash-Flood-Warning_32x32.png',             mid: 'icons/Flash-Flood-Watch_32x32.png',           low: 'icons/Flood-Statement_32x32.png'                    },
  deprem:  { high: 'icons/Earthquake-Warning_32x32.png',              mid: 'icons/Earthquake-Warning_32x32.png',          low: 'icons/Earthquake-Statement_32x32.png'               },
  yangin:  { high: 'icons/Fire-Warning_32x32.png',                    mid: 'icons/Fire-Warning_32x32.png',                low: 'icons/Severe-Weather-Statement_32x32.png'           },
  heyelan: { high: 'icons/Avalanche-Warning_32x32.png',               mid: 'icons/Avalanche-Watch_32x32.png',             low: 'icons/Severe-Weather-Statement_32x32.png'           },
  firtina: { high: 'icons/Severe-Thunderstorm-Warning_32x32.png',     mid: 'icons/Severe-Thunderstorm-Watch_32x32.png',   low: 'icons/Severe-Weather-Statement_32x32.png'           },
  kar:     { high: 'icons/Winter-Storm-Warning_32x32.png',            mid: 'icons/Winter-Storm-Warning_32x32.png',        low: 'icons/Severe-Weather-Statement_32x32.png'           },
  default: { high: 'icons/Civil-Emergency-Message-Warning_32x32.png', mid: 'icons/Civil-Emergency-Message-Warning_32x32.png', low: 'icons/Civil-Emergency-Message-Warning_32x32.png' },
};

const _DTYPE_MAP = [
  ['sel','sel'],['taşkın','sel'],['su baskın','sel'],['flood','sel'],
  ['deprem','deprem'],['earthquake','deprem'],
  ['yangın','yangin'],['yangin','yangin'],['fire','yangin'],
  ['heyelan','heyelan'],['çığ','heyelan'],['avalanche','heyelan'],
  ['fırtına','firtina'],['firtina','firtina'],['storm','firtina'],['thunderstorm','firtina'],
  ['kar','kar'],['snow','kar'],
];

function _detectDisasterType(report) {
  const text = ((report.processed_disaster_type || '') + ' ' + (report.disaster_description || '')).toLowerCase();
  for (const [kw, type] of _DTYPE_MAP) {
    if (text.includes(kw)) return type;
  }
  return 'default';
}

function getReportPinIcon(report) {
  const type  = _detectDisasterType(report);
  const level = getPinLevel(report.severity_score ?? null);
  const icons = DISASTER_ICONS[type] || DISASTER_ICONS.default;
  if (level === 'high')   return icons.high;
  if (level === 'medium') return icons.mid;
  return icons.low;
}

const CLUSTER_FLOOD_ICON = 'icons/HAZARD_NATHAZ_WHITE__SOLID-_DETAIL_Hazard--Flood_32x32.png';

// ---- Tarih format ----
function fmtDate(d) {
  if (!d) return '';
  const dt = new Date(d);
  if (isNaN(dt)) return d;
  return dt.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit' })
    + ' ' + dt.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
}

// ============================================================
// VER\u0130 \u00c7EKME
// ============================================================
async function fetchReports() {
  try {
    const res = await fetch(`${OASIS_API}/api/reports/?page=1&per_page=200`, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return data.reports || [];
  } catch (e) {
    console.warn('[OASIS] reports fetch failed:', e.message);
    return null;
  }
}

async function fetchClusters() {
  try {
    // T\u00fcm T\u00fcrkiye kapsayacak geni\u015f sorgu — Turkey merkezi
    const res = await fetch(
      `${OASIS_API}/api/clusters/nearby?lat=39.0&lon=35.0&radius=1500000`,
      { signal: AbortSignal.timeout(8000) }
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return data.clusters || [];
  } catch (e) {
    console.warn('[OASIS] clusters fetch failed:', e.message);
    return null;
  }
}

// ============================================================
// HAR\u0130TA P\u0130N'LER\u0130
// ============================================================
function clearEntities(arr) {
  arr.forEach(e => { try { viewer.entities.remove(e); } catch {} });
  arr.length = 0;
}

var _reportPinsVisible = true;
var _clusterPinsVisible = false;

function addReportPins(reports) {
  if (!viewer) return;
  clearEntities(_disasterEntities);
  if (!_reportPinsVisible) { viewer.scene.requestRender(); return; }

  reports.forEach(r => {
    if (!r.latitude || !r.longitude) return;
    const img = getReportPinIcon(r);

    const ent = viewer.entities.add({
      position: Cesium.Cartesian3.fromDegrees(
        parseFloat(r.longitude),
        parseFloat(r.latitude),
        5
      ),
      billboard: {
        image:           img,
        width:           29,
        height:          29,
        verticalOrigin:  Cesium.VerticalOrigin.BOTTOM,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      _oasisType: 'report',
      _oasisData: r,
    });

    _disasterEntities.push(ent);
  });
  viewer.scene.requestRender();
}

function _clusterColor(c) {
  const t = (c.event_type || '').toLowerCase();
  if (t.includes('yangın'))               return { fill: '#ff4500', outline: '#ff6a00' };
  if (t.includes('heyelan'))              return { fill: '#a16207', outline: '#ca8a04' };
  if (t.includes('deprem'))               return { fill: '#7c3aed', outline: '#a855f7' };
  if (t.includes('gaz'))                  return { fill: '#d97706', outline: '#f59e0b' };
  if (t.includes('elektrik'))             return { fill: '#eab308', outline: '#fde047' };
  if (t.includes('yapı') || t.includes('bina')) return { fill: '#b45309', outline: '#d97706' };
  return { fill: '#1d4ed8', outline: '#3b82f6' }; // sel default
}

function _clusterRadius(c) {
  const sev = c.severity_avg || 0;
  return sev >= 6 ? 50 : 30;
}

function addClusterPins(clusters) {
  if (!viewer) return;
  clearEntities(_clusterEntities);
  if (!_clusterPinsVisible) { viewer.scene.requestRender(); return; }

  clusters.forEach(c => {
    if (!c.center_lat || !c.center_lon) return;
    if (!_passesTimeFilterCluster(c)) return;

    const radM = _clusterRadius(c);
    const lon  = parseFloat(c.center_lon);
    const lat  = parseFloat(c.center_lat);
    const cc   = _clusterColor(c);
    const fillColor    = Cesium.Color.fromCssColorString(cc.fill).withAlpha(0.30);
    const outlineColor = Cesium.Color.fromCssColorString(cc.outline).withAlpha(0.95);

    // Dolu fill ellipse (kategori rengi)
    const fill = viewer.entities.add({
      position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
      ellipse: {
        semiMajorAxis:      radM,
        semiMinorAxis:      radM,
        material:           fillColor,
        outline:            false,
        heightReference:    Cesium.HeightReference.CLAMP_TO_GROUND,
        classificationType: Cesium.ClassificationType.TERRAIN,
      },
      _oasisType: 'cluster',
      _oasisData: c,
    });
    _clusterEntities.push(fill);

    // Dashed polyline çember (kategori rengi)
    const circlePts = generateCirclePositions(lon, lat, radM);
    const border = viewer.entities.add({
      polyline: {
        positions:    Cesium.Cartesian3.fromDegreesArray(circlePts),
        width:        2,
        material:     new Cesium.PolylineDashMaterialProperty({
          color:      outlineColor,
          dashLength: 14,
          dashPattern: 0xFF00,
        }),
        clampToGround: true,
      },
      _oasisType: 'cluster',
      _oasisData: c,
    });
    _clusterEntities.push(border);

    // Etiket
    const label = viewer.entities.add({
      position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
      label: {
        text:            `${c.event_type || 'küme'}  ${c.report_count}`,
        font:            '11px Segoe UI',
        fillColor:       Cesium.Color.WHITE,
        outlineColor:    Cesium.Color.BLACK,
        outlineWidth:    2,
        style:           Cesium.LabelStyle.FILL_AND_OUTLINE,
        pixelOffset:     new Cesium.Cartesian2(0, -(radM * 0.6 + 14)),
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        show:            true,
        scale:           0.9,
      },
      _oasisType: 'cluster',
      _oasisData: c,
    });
    _clusterEntities.push(label);
  });
  viewer.scene.requestRender();
}

// ============================================================
// SEL S\u0130M\u00dcLASYONU \u2014 Su Katman\u0131
// ============================================================

function getFloodClusters() {
  return _allClusters.filter(c => {
    if (!c.event_type) return false;
    return c.event_type.toLowerCase().includes('sel');
  });
}

function clearFloodLayer() {
  _floodEntities.forEach(e => {
    try { viewer.entities.remove(e); } catch {}
  });
  _floodEntities = [];
}

function generateCirclePositions(centerLon, centerLat, radiusMeters, segments = 64) {
  const positions = [];
  const earthRadius = 6378137;
  const latRad = centerLat * Math.PI / 180;
  for (let i = 0; i <= segments; i++) {
    const angle = (2 * Math.PI * i) / segments;
    const dLat = (radiusMeters * Math.cos(angle)) / earthRadius;
    const dLon = (radiusMeters * Math.sin(angle)) / (earthRadius * Math.cos(latRad));
    positions.push(centerLon + dLon * 180 / Math.PI);
    positions.push(centerLat + dLat * 180 / Math.PI);
  }
  return positions;
}

function renderFloodLayer() {
  if (!viewer) return;
  clearFloodLayer();

  const floodClusters = getFloodClusters();
  if (floodClusters.length === 0 || !_floodEnabled) return;

  floodClusters.forEach(c => {
    const lon = parseFloat(c.center_lon);
    const lat = parseFloat(c.center_lat);
    // Cluster backend'den gelen radius_meters kullan (100-300m)
    const radius = c.radius_meters || 150;

    const circlePositions = generateCirclePositions(lon, lat, radius);
    const hierarchy = new Cesium.PolygonHierarchy(
      Cesium.Cartesian3.fromDegreesArray(circlePositions)
    );
    const boundaryPositions = Cesium.Cartesian3.fromDegreesArray(circlePositions);

    // Yarı opak dolgu — "tahmini etki alanı" hissini verir
    const fillEntity = viewer.entities.add({
      polygon: {
        hierarchy: hierarchy,
        height: new Cesium.CallbackProperty(() => _floodWaterHeight, false),
        extrudedHeight: new Cesium.CallbackProperty(() => _floodWaterHeight + 0.01, false),
        material: new Cesium.ColorMaterialProperty(
          new Cesium.CallbackProperty(() => {
            const t = Math.min(_floodWaterHeight / 5, 1);
            const alpha = 0.08 + t * 0.12; // 0.08 → 0.20
            return Cesium.Color.fromCssColorString('#237ACF').withAlpha(alpha);
          }, false)
        ),
        outline: false,
      },
      _oasisType: 'flood',
    });
    _floodEntities.push(fillEntity);

    // Dashed sınır çizgisi — belirsizliği görsel olarak ifade eder
    const borderEntity = viewer.entities.add({
      polyline: {
        positions: boundaryPositions,
        width: 2,
        material: new Cesium.PolylineDashMaterialProperty({
          color: Cesium.Color.fromCssColorString('#3b82f6').withAlpha(0.85),
          dashLength: 12,
        }),
        clampToGround: true,
      },
      _oasisType: 'flood',
    });
    _floodEntities.push(borderEntity);

    // "Tahmini Etki Alanı" etiketi — tahmin olduğunu açıkça belirtir
    const labelEntity = viewer.entities.add({
      position: Cesium.Cartesian3.fromDegrees(lon, lat, 5),
      label: {
        text: 'Tahmini Etki Alanı',
        font: '11px Segoe UI',
        fillColor: Cesium.Color.fromCssColorString('#60a5fa'),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        pixelOffset: new Cesium.Cartesian2(0, -8),
      },
      _oasisType: 'flood',
    });
    _floodEntities.push(labelEntity);
  });

  updateFloodStats(floodClusters);
  viewer.scene.requestRender();
}

function updateFloodStats(floodClusters) {
  const countEl = document.getElementById('floodClusterCount');
  const areaEl  = document.getElementById('floodAreaCount');
  if (countEl) countEl.textContent = floodClusters.length;
  if (areaEl) {
    const totalArea = floodClusters.reduce((sum, c) => {
      const r = (c.radius_meters || 150) / 1000;
      return sum + Math.PI * r * r;
    }, 0);
    areaEl.textContent = totalArea.toFixed(2);
  }
}

function toggleFloodSimulation() {
  _floodEnabled = !_floodEnabled;
  const inline = document.getElementById('floodInline');
  const btn   = document.getElementById('btn-flood');

  if (_floodEnabled) {
    const fc = getFloodClusters();
    if (fc.length === 0) {
      console.warn('[OASIS] Sel k\u00fcmesi bulunamad\u0131');
      _floodEnabled = false;
      return;
    }
    if (inline) inline.classList.add('open');
    btn.classList.add('active-layer');
    renderFloodLayer();
  } else {
    if (inline) inline.classList.remove('open');
    btn.classList.remove('active-layer');
    clearFloodLayer();
    const slider = document.getElementById('floodSlider');
    const valEl  = document.getElementById('floodLevelVal');
    if (slider) slider.value = 0;
    if (valEl)  valEl.firstChild.textContent = '0.0';
    _floodWaterHeight = 0;
  }
  viewer.scene.requestRender();
}

function initFloodSlider() {
  const slider = document.getElementById('floodSlider');
  const valEl  = document.getElementById('floodLevelVal');
  if (!slider) return;

  slider.addEventListener('input', (e) => {
    const val = parseFloat(e.target.value);
    _floodWaterHeight = val;
    if (valEl) valEl.firstChild.textContent = val.toFixed(1);
    if (viewer) viewer.scene.requestRender();
  });
}

// Pin t\u0131klama tooltip \u2014 mevcut setupPickingHandler()'a ek olarak \u00e7al\u0131\u015f\u0131r
function setupOasisPicking() {
  if (!viewer) return;
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

  handler.setInputAction(movement => {
    const pick = viewer.scene.pick(movement.position);
    if (!Cesium.defined(pick) || !pick.id) return;

    const ent = pick.id;
    if (!ent._oasisType) return;

    const d = ent._oasisData;
    if (ent._oasisType === 'report') {
      flyToReport(parseFloat(d.longitude), parseFloat(d.latitude));
      showReportDetail(d);
      if (_activeTab !== 'reports') switchTab('reports');
      const el = document.getElementById(`rcard-${d.id}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    } else if (ent._oasisType === 'cluster') {
      flyToReport(parseFloat(d.center_lon), parseFloat(d.center_lat));
      if (_activeTab !== 'clusters') switchTab('clusters');
      const el = document.getElementById(`ccard-${d.id}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
}

// ============================================================
// PANEL UI
// ============================================================
function flyToReport(lon, lat) {
  if (!viewer) return;
  // Pitch açısından kaynaklanan görsel kayma telafisi (~0.00058° güneye kaydır)
  const adjustedLat = lat - 0.00058;
  viewer.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(lon, adjustedLat, 60),
    orientation: {
      heading: Cesium.Math.toRadians(0),
      pitch:   Cesium.Math.toRadians(-45),
      roll:    0,
    },
  });
  viewer.scene.requestRender();
}

// flyHome: Ordu/Akyazı başlangıç konumuna dön
function flyHome() {
  if (!viewer) return;
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(37.91462, 40.97621, 539),
    orientation: {
      heading: 0,
      pitch:   Cesium.Math.toRadians(-45),
      roll:    0,
    },
    duration: 1.8,
    easingFunction: Cesium.EasingFunction.QUADRATIC_IN_OUT,
  });
}

// Detay paneli: API'den rapor detayı çek ve göster
async function showReportDetail(data) {
  const panel   = document.getElementById('detailPanel');
  const content = document.getElementById('detailContent');
  if (!panel || !content) return;

  // Paneli aç, loading göster
  content.innerHTML = '<div style="padding:30px;text-align:center"><div class="spinner" style="width:24px;height:24px;border-width:2px;margin:0 auto 8px"></div><p style="font-size:11px;color:#6b7280">Yükleniyor...</p></div>';
  panel.classList.add('open');

  try {
    const rid = data.report_id || data.id;
    const res = await fetch(`${OASIS_API}/api/reports/${rid}/detail`, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const detail = await res.json();

    const report    = detail.report    || {};
    const processed = detail.processed || {};
    const exif      = detail.exif      || {};

    // Görsel
    let imgHtml = '<div class="no-img">Görsel yok</div>';
    if (report.image_path) {
      imgHtml = `<img src="${OASIS_API}/api/images/${report.image_path}" alt="Afet görseli" onerror="this.parentElement.innerHTML='<div class=no-img>Görsel yüklenemedi</div>'">`;
    }

    const disType = processed.disaster_type || report.event_define || 'Bilinmiyor';
    const sev     = processed.severity_score;

    // ── LLM özet metni ──
    let llmText = '';
    if (processed.llm_analysis) {
      const llm = typeof processed.llm_analysis === 'string' ? JSON.parse(processed.llm_analysis) : processed.llm_analysis;
      llmText = (llm.summary || llm.description || '');
      if (!llmText && llm.llm) llmText = llm.llm?.summary || '';
    }

    // ── VLM görsel açıklama ──
    let vlmText = '';
    if (processed.vlm_analysis) {
      const vlm = typeof processed.vlm_analysis === 'string' ? JSON.parse(processed.vlm_analysis) : processed.vlm_analysis;
      vlmText = vlm.visual_description || vlm.description || vlm.summary || '';
    }

    // ── Gösterge-tabanlı kategori puanları ──
    let indicatorHtml = '';
    const catScores = processed.category_scores;
    const activeInd = processed.active_indicators || [];
    const crossVal  = processed.cross_validated || [];
    const formula   = processed.formula_breakdown || '';
    const reliability = processed.reliability_score;

    if (catScores) {
      const catLabels = {human: 'İnsan', infra: 'Altyapı', enviro: 'Çevre', comms: 'İletişim'};
      const catMax    = {human: 4.0, infra: 3.0, enviro: 2.0, comms: 1.0};
      const catColors = {human: '#ef4444', infra: '#f97316', enviro: '#22c55e', comms: '#3b82f6'};
      let barsHtml = '';
      for (const [cat, label] of Object.entries(catLabels)) {
        const val = catScores[cat] || 0;
        const max = catMax[cat];
        const pct = Math.round((val / max) * 100);
        const col = catColors[cat];
        barsHtml += `
          <div style="display:flex;align-items:center;gap:6px;margin:2px 0">
            <span style="width:52px;font-size:10px;color:#9ca3af;text-align:right">${label}</span>
            <div style="flex:1;height:6px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden">
              <div style="width:${pct}%;height:100%;background:${col};border-radius:3px;transition:width .3s"></div>
            </div>
            <span style="width:28px;font-size:10px;color:#d1d5db;font-weight:600">${val}</span>
          </div>`;
      }

      // Aktif göstergeler listesi
      const indLabels = {
        people_trapped:'Mahsur kişi', people_injured:'Yaralı', life_threat:'Hayati tehlike',
        children_elderly_at_risk:'Çocuk/yaşlı risk', large_crowd_affected:'Çok kişi etkilendi',
        building_collapsed:'Bina çökmüş', building_damaged:'Bina hasarlı', road_blocked:'Yol kapanmış',
        utility_disrupted:'Altyapı kesilmiş', utility_dangerous:'Altyapı tehlikeli',
        flood_water_rising:'Su yükseliyor', fire_active:'Aktif yangın', landslide_active:'Heyelan',
        hazmat_present:'Tehlikeli madde', aftershock_risk:'Artçı deprem riski',
        no_communication:'İletişim yok', area_isolated:'Bölge izole', rescue_requested:'Kurtarma talebi'
      };
      let tagsHtml = '';
      activeInd.forEach(ind => {
        const isCross = crossVal.includes(ind);
        const lbl = indLabels[ind] || ind;
        tagsHtml += `<span style="display:inline-block;padding:1px 6px;margin:1px;border-radius:3px;font-size:9px;font-weight:600;background:${isCross ? 'rgba(34,197,94,0.25)' : 'rgba(255,255,255,0.1)'};color:${isCross ? '#22c55e' : '#d1d5db'};border:1px solid ${isCross ? 'rgba(34,197,94,0.3)' : 'rgba(255,255,255,0.08)'}" title="${isCross ? 'LLM+VLM çapraz doğrulandı' : 'Tek kaynak'}">${lbl}${isCross ? ' ✓' : ''}</span>`;
      });

      // Güvenilirlik barı
      let reliabilityHtml = '';
      if (reliability != null) {
        const relPct = Math.round(reliability * 100);
        const relCol = relPct >= 80 ? '#22c55e' : relPct >= 60 ? '#eab308' : '#ef4444';
        const relLabel = relPct >= 80 ? 'Yüksek' : relPct >= 60 ? 'Orta' : 'Düşük';
        reliabilityHtml = `
          <div style="display:flex;align-items:center;gap:6px;margin:6px 0 2px">
            <span style="width:52px;font-size:10px;color:#9ca3af;text-align:right">Güven</span>
            <div style="flex:1;height:6px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden">
              <div style="width:${relPct}%;height:100%;background:${relCol};border-radius:3px;transition:width .3s"></div>
            </div>
            <span style="width:52px;font-size:10px;color:${relCol};font-weight:600">${relLabel} %${relPct}</span>
          </div>
          <div style="font-size:8px;color:#6b7280;margin-left:58px">${crossVal.length}/${activeInd.length} gösterge çapraz doğrulandı</div>`;
      }

      indicatorHtml = `
        <div class="detail-section">
          <div class="detail-section-title">Gösterge Analizi</div>
          <div style="margin:6px 0">${barsHtml}</div>
          ${reliabilityHtml}
          ${tagsHtml ? `<div style="margin-top:6px;line-height:1.8">${tagsHtml}</div>` : ''}
          ${formula ? `<div style="margin-top:6px;font-size:9px;color:#6b7280;font-family:monospace">${formula}</div>` : ''}
        </div>`;
    }

    content.innerHTML = `
      <div class="detail-img-wrap">
        ${imgHtml}
        <span class="detail-badge">${disType}</span>
      </div>
      <div class="detail-body">
        <div class="detail-title">${report.report_id || '#' + report.id}</div>
        <div class="detail-subtitle">${report.user_behavior === 'victim' ? 'Afetzede' : report.user_behavior === 'observer' ? 'Gözlemci' : (report.user_behavior || '')} ${processed.user_status ? '• ' + processed.user_status : ''} — ${fmtDate(report.report_date)}</div>
        <div class="detail-grid">
          <div class="detail-field"><div class="detail-field-lbl">Aciliyet Puanı</div><div class="detail-field-val" style="font-size:16px;font-weight:700;color:${sev >= 7 ? '#ef4444' : sev >= 4 ? '#eab308' : '#22c55e'}">${sev != null ? sev.toFixed(1) : '—'}<span style="font-size:10px;color:#6b7280;font-weight:400"> /10</span></div></div>
          <div class="detail-field"><div class="detail-field-lbl">Durum</div><div class="detail-field-val">${processed.user_status || '—'}</div></div>
          <div class="detail-field"><div class="detail-field-lbl">Rol</div><div class="detail-field-val">${report.user_behavior === 'victim' ? 'Afetzede' : report.user_behavior === 'observer' ? 'Gözlemci' : '—'}</div></div>
          <div class="detail-field"><div class="detail-field-lbl">Cihaz</div><div class="detail-field-val">${exif.device || '—'}</div></div>
          <div class="detail-field"><div class="detail-field-lbl">Konum Eşl.</div><div class="detail-field-val">${processed.location_match_score != null ? (processed.location_match_score * 100).toFixed(0) + '%' : '—'}</div></div>
          <div class="detail-field"><div class="detail-field-lbl">İletişim</div><div class="detail-field-val">${report.can_communicate ? 'Evet' : 'Hayır'}</div></div>
        </div>
        ${indicatorHtml}
        ${llmText ? `<div class="detail-section"><div class="detail-section-title">LLM Özet</div><div class="detail-text">${llmText}</div></div>` : ''}
        ${vlmText ? `<div class="detail-section"><div class="detail-section-title">VLM Görsel Analiz</div><div class="detail-text">${vlmText}</div></div>` : ''}
       
      </div>
    `;
  } catch (err) {
    content.innerHTML = `<div style="padding:30px;text-align:center;color:#6b7280"><p>Detay yüklenemedi</p><p style="font-size:11px;margin-top:4px">${err.message}</p></div>`;
  }
}

function closeDetail() {
  const panel = document.getElementById('detailPanel');
  if (panel) panel.classList.remove('open');
}

function onCardClick(reportId, lon, lat) {
  flyToReport(lon, lat);
  const report = _allReports.find(r => String(r.id) === String(reportId));
  if (report) showReportDetail(report);
}

function switchTab(tab) {
  _activeTab = tab;
  document.getElementById('tab-reports').classList.toggle('active',  tab === 'reports');
  document.getElementById('tab-clusters').classList.toggle('active', tab === 'clusters');
  renderList();
}

function showUnprocessedReports() {
  _activeTab = 'reports';
  document.getElementById('tab-reports').classList.add('active');
  document.getElementById('tab-clusters').classList.remove('active');

  _showUnprocessedOnly = true;
  setAllPinLevels(true);
  _timeFilterHours = 0;
  _timeFilterFrom = null;
  _timeFilterTo = null;

  const fromEl = document.getElementById('tfDateFrom');
  const toEl   = document.getElementById('tfDateTo');
  if (fromEl) fromEl.value = '';
  if (toEl) toEl.value = '';
  document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.hours, 10) === 0);
  });

  const filtered = getFilteredReports();
  addReportPins(filtered);
  applyBuildingUrgencyZones(filtered);
  renderList();
}

function updateStats(reports, clusters) {
  const totalEl = document.getElementById('dp-total');
  if (totalEl) totalEl.textContent = reports.length;

  const clustersEl = document.getElementById('dp-clusters');
  if (clustersEl) clustersEl.textContent = clusters.length;

  const sevEl = document.getElementById('dp-severity');
  if (sevEl) {
    const sevs = reports.map(r => r.severity_score || r.nearby_cluster?.severity_avg || 0).filter(Boolean);
    const maxSev = sevs.length ? Math.max(...sevs) : 0;
    sevEl.textContent = maxSev ? maxSev.toFixed(1) : '\u2014';
  }

  const lastUpdateEl = document.getElementById('dp-last-update');
  if (lastUpdateEl) {
    lastUpdateEl.textContent = `Son guncelleme: ${fmtDate(_lastRefreshAt || new Date())}`;
  }
}

function renderList() {
  const list = document.getElementById('dp-list');
  if (!list) return;

  if (_activeTab === 'reports') {
    const filteredReports = getFilteredReports();
    if (!filteredReports.length) {
      list.innerHTML = '<div class="dp-empty">Seçili durumlarda bildirim yok</div>';
      return;
    }
    list.innerHTML = filteredReports.map(r => {
      const ec  = getEventColor(r.processed_disaster_type || r.event_define);
      const sc  = getSevClass(r.severity_score || r.nearby_cluster?.severity_avg);
      const sev = r.severity_score || r.nearby_cluster?.severity_avg;
      const sevPct = sev ? Math.min(100, sev * 10) : 0;

      // Olay türüne göre sol kenar rengi (sel=mavi, deprem=kırmızı, yangın=turuncu, vb.)
      const borderColor = ec.bg;

      // Konum doğruluk yüzdesi
      const locAccuracy = r.processed_location_match_score != null
        ? Math.round(r.processed_location_match_score * 100)
        : (r.location_match_score != null ? Math.round(r.location_match_score * 100) : null);
      const locHtml = locAccuracy != null
        ? `<span style="display:inline-flex;align-items:center;gap:2px;font-size:9px;color:var(--td);margin-left:6px">📍 %${locAccuracy}</span>`
        : '';

      // user_behavior + user_status
      const bhvr = r.user_behavior === 'victim' ? 'Afetzede' : r.user_behavior === 'observer' ? 'Gözlemci' : (r.user_behavior || '');
      const uStatus = r.processed_user_status || '';
      const idLine = [bhvr, uStatus].filter(Boolean).join(' • ');

      // LLM summary or fallback (event_define kullanıcı açıklaması)
      const summary = r.processed_summary || r.event_define || 'Açıklama yok';

      return `<div class="dp-card ${sc}" id="rcard-${r.id}" onclick="onCardClick('${r.id}', ${parseFloat(r.longitude)}, ${parseFloat(r.latitude)})" style="--card-border:${borderColor}">
        <div class="dp-card-top">
          <span class="dp-card-label">${r.processed_disaster_type || ec.label}</span>
          <span style="display:flex;align-items:center"><span class="dp-card-time">${fmtDate(r.report_date)}</span>${locHtml}</span>
        </div>
        <div class="dp-card-id">${idLine}</div>
        <div class="dp-card-title">${summary}</div>
        ${sev ? `<div class="dp-card-sev">
          <div class="sev-bar-wrap"><div class="sev-bar" style="width:${sevPct}%"></div></div>
          <span class="dp-card-sev-val">${sev.toFixed ? sev.toFixed(1) : sev}</span>
          ${r.active_indicators && r.active_indicators.length ? `<span style="font-size:8px;color:#9ca3af;margin-left:4px">${r.active_indicators.length} gst</span>` : ''}
          ${r.reliability_score != null ? `<span style="font-size:8px;margin-left:3px;color:${r.reliability_score >= 0.8 ? '#22c55e' : r.reliability_score >= 0.6 ? '#eab308' : '#ef4444'}" title="Güvenilirlik: %${Math.round(r.reliability_score*100)}">${r.reliability_score >= 0.8 ? '●' : r.reliability_score >= 0.6 ? '◐' : '○'}</span>` : ''}
        </div>` : ''}
      </div>`;
    }).join('');
  } else {
    const filteredClusters = _allClusters.filter(c => _passesTimeFilterCluster(c));
    if (!filteredClusters.length) {
      list.innerHTML = '<div class="dp-empty">Küme tespit edilmedi</div>';
      return;
    }
    list.innerHTML = filteredClusters.map(c => {
      const ec = getEventColor(c.event_type);
      const lockIcon = c.is_locked ? '' : '';
      const lockTitle = c.is_locked ? 'Merkez kilitli (≥3 rapor)' : 'Merkez hareketli (<3 rapor)';
      return `<div class="dp-cluster-card" id="ccard-${c.id}" onclick="flyToReport(${parseFloat(c.center_lon)},${parseFloat(c.center_lat)})">
        <div class="dp-cluster-top">
          <span class="dp-cluster-badge">KÜME</span>
          <span class="dp-cluster-count">${c.report_count} bildirim</span>
          <span title="${lockTitle}" style="margin-left:auto;font-size:11px;cursor:help">${lockIcon}</span>
        </div>
        <div class="dp-cluster-type">${ec.label} Kümesi</div>
        <div class="dp-cluster-meta">
          Ort. Şiddet: <strong>${c.severity_avg ? c.severity_avg.toFixed(1) : '—'}</strong> &nbsp;•&nbsp;
          Buffer: <strong>${c.radius_meters ? Math.round(c.radius_meters) : 100}m</strong> &nbsp;•&nbsp;
          ${fmtDate(c.last_report_at)}
        </div>
      </div>`;
    }).join('');
  }
}

// ============================================================
// RAPORLAR SEKMESİ — Pipeline Stage Görüntüleme
// ============================================================
var _pipelineStage = 'raw'; // raw | processed | clusters | all
var _pipelineEntities = [];  // Bu sekme için ayrı pin entity listesi

function switchPipelineStage(stage) {
  _pipelineStage = stage;
  document.querySelectorAll('.rp-stage-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.stage === stage);
  });
  renderPipelineList();
  renderPipelinePins();
}

function makeRawPin() {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="38" viewBox="0 0 28 38">
    <defs><filter id="dsr" x="-30%" y="-20%" width="160%" height="160%">
      <feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#000" flood-opacity="0.55"/></filter></defs>
    <g filter="url(#dsr)">
      <path d="M14 2 C7.37 2 2 7.37 2 14 C2 22.5 14 36 14 36 C14 36 26 22.5 26 14 C26 7.37 20.63 2 14 2 Z"
        fill="#94a3b8" stroke="rgba(255,255,255,0.40)" stroke-width="1.5"/>
      <circle cx="14" cy="14" r="5" fill="rgba(255,255,255,0.80)"/>
      <text x="14" y="14" text-anchor="middle" dominant-baseline="central" font-size="6" font-weight="800" fill="#475569">R</text>
    </g></svg>`;
  return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)));
}

function makeProcessedPin(severity) {
  const color = severity >= 8 ? '#dc2626' : severity >= 5 ? '#eab308' : '#22c55e';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="38" viewBox="0 0 28 38">
    <defs><filter id="dsp" x="-30%" y="-20%" width="160%" height="160%">
      <feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#000" flood-opacity="0.55"/></filter></defs>
    <g filter="url(#dsp)">
      <path d="M14 2 C7.37 2 2 7.37 2 14 C2 22.5 14 36 14 36 C14 36 26 22.5 26 14 C26 7.37 20.63 2 14 2 Z"
        fill="${color}" stroke="rgba(255,255,255,0.50)" stroke-width="1.5"/>
      <circle cx="14" cy="14" r="5" fill="rgba(255,255,255,0.90)"/>
      <text x="14" y="14" text-anchor="middle" dominant-baseline="central" font-size="7" font-weight="800" fill="${color}">${Math.round(severity)}</text>
    </g></svg>`;
  return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)));
}

function clearPipelineEntities() {
  _pipelineEntities.forEach(e => { try { viewer.entities.remove(e); } catch {} });
  _pipelineEntities.length = 0;
}

function renderPipelinePins() {
  if (!viewer) return;
  clearPipelineEntities();

  // Mevcut Afet İzleme pinlerini gizle/göster
  const isReportsTab = document.getElementById('pane-reports')?.classList.contains('active');
  _disasterEntities.forEach(e => { try { e.show = !isReportsTab; } catch {} });
  _clusterEntities.forEach(e => { try { e.show = !isReportsTab; } catch {} });

  if (!isReportsTab) { viewer.scene.requestRender(); return; }

  // Raw = TÜM raporların ham hali (raw_reports tablosu)
  // Processed = severity_score dolmuş olanlar (processed_reports tablosu)
  const allRaw = _allReports;  // Tüm raporlar raw_reports'tan geliyor
  const procReports = _allReports.filter(r => r.severity_score != null);
  const clusters = _allClusters;

  const showRaw = _pipelineStage === 'raw' || _pipelineStage === 'all';
  const showProc = _pipelineStage === 'processed' || _pipelineStage === 'all';
  const showClust = _pipelineStage === 'clusters' || _pipelineStage === 'all';

  if (showRaw) {
    allRaw.forEach(r => {
      if (!r.latitude || !r.longitude) return;
      const ent = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(parseFloat(r.longitude), parseFloat(r.latitude), 5),
        billboard: { image: makeRawPin(), width: 18, height: 24, verticalOrigin: Cesium.VerticalOrigin.BOTTOM, heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY },
        _oasisType: 'report', _oasisData: r,
      });
      _pipelineEntities.push(ent);
    });
  }

  if (showProc) {
    procReports.forEach(r => {
      if (!r.latitude || !r.longitude) return;
      const sev = r.severity_score || 0;
      const ent = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(parseFloat(r.longitude), parseFloat(r.latitude), 5),
        billboard: { image: makeProcessedPin(sev), width: 18, height: 25, verticalOrigin: Cesium.VerticalOrigin.BOTTOM, heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY },
        _oasisType: 'report', _oasisData: r,
      });
      _pipelineEntities.push(ent);
    });
  }

  if (showClust) {
    clusters.forEach(c => {
      if (!c.center_lat || !c.center_lon) return;
      const radM = _clusterRadius(c);
      const lon  = parseFloat(c.center_lon);
      const lat  = parseFloat(c.center_lat);

      const fillEnt = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
        ellipse: {
          semiMajorAxis:      radM,
          semiMinorAxis:      radM,
          material:           Cesium.Color.WHITE.withAlpha(0.18),
          outline:            false,
          heightReference:    Cesium.HeightReference.CLAMP_TO_GROUND,
          classificationType: Cesium.ClassificationType.TERRAIN,
        },
        _oasisType: 'cluster', _oasisData: c,
      });
      _pipelineEntities.push(fillEnt);

      const circlePts = generateCirclePositions(lon, lat, radM);
      const borderEnt = viewer.entities.add({
        polyline: {
          positions:    Cesium.Cartesian3.fromDegreesArray(circlePts),
          width:        2,
          material:     new Cesium.PolylineDashMaterialProperty({
            color:      Cesium.Color.WHITE.withAlpha(0.90),
            dashLength: 14,
            dashPattern: 0xFF00,
          }),
          clampToGround: true,
        },
        _oasisType: 'cluster', _oasisData: c,
      });
      _pipelineEntities.push(borderEnt);

      const labelEnt = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
        label: {
          text:            `${c.event_type || 'küme'}  ${c.report_count}`,
          font:            '11px Segoe UI',
          fillColor:       Cesium.Color.WHITE,
          outlineColor:    Cesium.Color.BLACK,
          outlineWidth:    2,
          style:           Cesium.LabelStyle.FILL_AND_OUTLINE,
          pixelOffset:     new Cesium.Cartesian2(0, -(radM * 0.6 + 14)),
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          show:            true,
          scale:           0.9,
        },
        _oasisType: 'cluster', _oasisData: c,
      });
      _pipelineEntities.push(labelEnt);
    });
  }

  viewer.scene.requestRender();
}

function renderPipelineList() {
  const list = document.getElementById('rp-list');
  if (!list) return;

  // Raw = TÜM raporların ham hali (raw_reports tablosu)
  // Processed = severity_score dolmuş olanlar (processed_reports tablosu)
  const allRaw = _allReports;  // Tüm raporlar raw_reports'tan geliyor
  const procReports = _allReports.filter(r => r.severity_score != null);
  const clusters = _allClusters;

  // İstatistikleri güncelle
  const rc = document.getElementById('rp-raw-count');
  const pc = document.getElementById('rp-proc-count');
  const cc = document.getElementById('rp-clust-count');
  if (rc) rc.textContent = allRaw.length;
  if (pc) pc.textContent = procReports.length;
  if (cc) cc.textContent = clusters.length;

  let html = '';

  if (_pipelineStage === 'raw' || _pipelineStage === 'all') {
    if (_pipelineStage === 'all') html += '<div class="rp-section-label" style="color:#94a3b8">Raw Reports (' + allRaw.length + ')</div>';
    if (allRaw.length === 0 && _pipelineStage === 'raw') {
      html += '<div class="dp-empty">Ham rapor yok</div>';
    } else {
      html += allRaw.map(r => `<div class="rp-card raw" onclick="flyToReport(${parseFloat(r.longitude)},${parseFloat(r.latitude)})">
        <div class="rp-card-header">
          <span class="rp-card-badge raw">RAW</span>
          <span class="rp-card-time">${fmtDate(r.report_date)}</span>
        </div>
        <div class="rp-card-id">${r.report_id}</div>
        <div class="rp-card-desc">${r.event_define || 'Açıklama yok'}</div>
      </div>`).join('');
    }
  }

  if (_pipelineStage === 'processed' || _pipelineStage === 'all') {
    if (_pipelineStage === 'all') html += '<div class="rp-section-label" style="color:#22c55e">Processed Reports (' + procReports.length + ')</div>';
    if (procReports.length === 0 && _pipelineStage === 'processed') {
      html += '<div class="dp-empty">İşlenmiş rapor yok — analyze-all çalıştırın</div>';
    } else {
      html += procReports.map(r => {
        const sev = r.severity_score || 0;
        const sevPct = Math.min(100, sev * 10);
        return `<div class="rp-card processed" onclick="onCardClick('${r.id}', ${parseFloat(r.longitude)}, ${parseFloat(r.latitude)})">
          <div class="rp-card-header">
            <span class="rp-card-badge processed">${r.processed_disaster_type || 'PROCESSED'}</span>
            <span class="rp-card-time">${fmtDate(r.report_date)}</span>
          </div>
          <div class="rp-card-id">${r.report_id} — ${r.processed_user_status || ''}</div>
          <div class="rp-card-desc">${r.processed_summary || r.event_define || 'Açıklama yok'}</div>
          <div class="dp-card-sev" style="margin-top:3px">
            <div class="sev-bar-wrap"><div class="sev-bar" style="width:${sevPct}%"></div></div>
            <span class="dp-card-sev-val">${sev.toFixed(1)}</span>
          </div>
        </div>`;
      }).join('');
    }
  }

  if (_pipelineStage === 'clusters' || _pipelineStage === 'all') {
    if (_pipelineStage === 'all') html += '<div class="rp-section-label" style="color:#a78bfa">Disaster Clusters (' + clusters.length + ')</div>';
    if (clusters.length === 0 && _pipelineStage === 'clusters') {
      html += '<div class="dp-empty">Küme yok — cluster-all çalıştırın</div>';
    } else {
      html += clusters.map(c => {
        const ec = getEventColor(c.event_type);
        return `<div class="rp-card cluster" onclick="flyToReport(${parseFloat(c.center_lon)},${parseFloat(c.center_lat)})">
          <div class="rp-card-header">
            <span class="rp-card-badge cluster">KÜME</span>
            <span class="rp-card-time">${c.report_count} bildirim</span>
          </div>
          <div class="rp-card-id">${ec.label} Kümesi</div>
          <div class="rp-card-desc">Ort. Şiddet: ${c.severity_avg ? c.severity_avg.toFixed(1) : '—'} • Buffer: ${c.radius_meters ? Math.round(c.radius_meters) : 100}m</div>
        </div>`;
      }).join('');
    }
  }

  list.innerHTML = html || '<div class="dp-empty">Henüz veri yok</div>';
}

// ============================================================
// ADMİN: TEST VERİSİ YÖNETİMİ
// ============================================================
async function adminPopulateTestData() {
  const btn = document.getElementById('btn-populate');
  if (btn) { btn.disabled = true; btn.textContent = 'Yükleniyor...'; }
  try {
    const res = await fetch(`${OASIS_API}/api/reports/admin/populate-test-data`, {
      method: 'POST',
      signal: AbortSignal.timeout(30000),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Sunucu hatası');
    alert(`✅ ${data.reports_inserted} rapor yüklendi, ${data.images_found} görsel eşlendi.`);
    await refreshDisasters();
  } catch (e) {
    alert('❌ Raporlar yüklenemedi: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Raporları Yükle'; }
  }
}

async function adminClearVisibility() {
  if (!confirm('Tüm analiz sonuçları ve kümeler silinecek, ham raporlar korunacak. Devam edilsin mi?')) return;
  const btn = document.getElementById('btn-clear-visibility');
  if (btn) { btn.disabled = true; btn.textContent = 'Temizleniyor...'; }
  try {
    const res = await fetch(`${OASIS_API}/api/reports/admin/clear-visibility`, {
      method: 'POST',
      signal: AbortSignal.timeout(15000),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Sunucu hatası');
    alert('✅ Dashboard temizlendi. Raporlar veritabanında korunuyor.');
    await refreshDisasters();
  } catch (e) {
    alert('❌ Temizleme başarısız: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Raporları Kaldır'; }
  }
}

// ============================================================
// MANUEL YENİLEME (Sidebar buton)
// ============================================================
async function manualRefresh() {
  const btn = document.getElementById('btn-refresh');
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = '0.5';
    btn.innerHTML = '<span style="display:inline-block;animation:spin .6s linear infinite">&#8635;</span> Yenile';
  }
  try {
    await refreshDisasters();
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = '&#8635; Yenile';
    }
  }
}

// ============================================================
// ANA YENILEME DÖNGÜSÜ
// ============================================================
async function refreshDisasters() {
  console.log('[OASIS] Veriler yenileniyor\u2026');

  if (!Array.isArray(_allReports))  _allReports  = [];
  if (!Array.isArray(_allClusters)) _allClusters = [];

  const prevCount = _allReports.length;
  let [reports, clusters] = await Promise.all([fetchReports(), fetchClusters()]);

  // Akyazı bbox dışındaki veriyi filtrele (UI kasmayı azaltır)
  if (Array.isArray(reports)) {
    const before = reports.length;
    reports = reports.filter(r => _inAkyaziBBox(r.latitude, r.longitude));
    if (before !== reports.length) console.log(`[OASIS] bbox filtre: ${before} → ${reports.length} rapor`);
  }
  if (Array.isArray(clusters)) {
    const before = clusters.length;
    clusters = clusters.filter(c => _inAkyaziBBox(c.center_lat, c.center_lon));
    if (before !== clusters.length) console.log(`[OASIS] bbox filtre: ${before} → ${clusters.length} küme`);
  }

  if (reports !== null) {
    // Hash karşılaştırma — değişiklik yoksa pin re-render atla
    const reportsHash = reports.length + ':' + reports.map(r => `${r.id}-${r.severity_score ?? 'x'}-${r.processed_disaster_type ?? 'x'}`).join(',');
    const reportsChanged = reportsHash !== _lastReportsHash;
    _allReports = reports;
    _lastRefreshAt = new Date();
    updateLegendCounts();
    _updateTimeFilterInfo();
    if (reportsChanged) {
      _lastReportsHash = reportsHash;
      const filtered = getFilteredReports();
      addReportPins(filtered);
      applyBuildingUrgencyZones(filtered);
      console.log('[OASIS] Rapor verisi değişti, pin re-render yapıldı');
    }
  }
  if (clusters !== null) {
    const clustersHash = clusters.length + ':' + clusters.map(c => `${c.id}-${c.report_count ?? 'x'}`).join(',');
    const clustersChanged = clustersHash !== _lastClustersHash;
    _allClusters = clusters;
    if (clustersChanged) {
      _lastClustersHash = clustersHash;
      addClusterPins(getFilteredClusters());
      if (_floodEnabled) renderFloodLayer();
      console.log('[OASIS] Küme verisi değişti, pin re-render yapıldı');
    }
  }

  updateStats(getFilteredReports(), _allClusters);
  updateRightSidebar();
  renderList();

  // Raporlar sekmesi açıksa güncelle
  if (document.getElementById('pane-reports')?.classList.contains('active')) {
    renderPipelineList();
    renderPipelinePins();
  } else {
    // Sadece istatistikleri güncelle (sekme kapalı olsa bile)
    const rc = document.getElementById('rp-raw-count');
    const pc = document.getElementById('rp-proc-count');
    const cc = document.getElementById('rp-clust-count');
    if (rc) rc.textContent = _allReports.length;
    if (pc) pc.textContent = _allReports.filter(r => r.severity_score != null).length;
    if (cc) cc.textContent = _allClusters.length;
  }

  if (reports !== null && prevCount > 0 && _allReports.length > prevCount) {
    console.log(`[OASIS] Yeni rapor algılandı (+${_allReports.length - prevCount}) — pinler otomatik güncellendi`);
  }
  _lastReportsCount = _allReports.length;

  console.log(`[OASIS] ${_allReports.length} bildirim, ${_allClusters.length} küme yüklendi`);
}

function startAutoRefresh(intervalMs = AUTO_REFRESH_MS) {
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(refreshDisasters, intervalMs);
}

// ============================================================
// INIT (Viewer haz\u0131r olduktan sonra \u00e7a\u011fr\u0131l\u0131r)
// ============================================================
async function initOasisOverlay() {
  if (!viewer) {
    console.warn('[OASIS] viewer hazır değil, 2sn bekleniyor…');
    setTimeout(initOasisOverlay, 2000);
    return;
  }
  setupOasisPicking();
  initFloodSlider();
  await refreshDisasters();
  startAutoRefresh(AUTO_REFRESH_MS);
}

// initViewer() tamamland\u0131ktan sonra overlay'i ba\u015flat
const _origInit = typeof initViewer === 'function' ? initViewer : null;
// initViewer zaten global; DOMContentLoaded i\u00e7inden \u00e7a\u011fr\u0131l\u0131yor,
// biz de DOMContentLoaded'da initOasisOverlay'i ko\u015fturuyoruz:
document.addEventListener('DOMContentLoaded', () => {
  // Akyazı bina footprint verisini arka planda yükle
  loadBuildingData();

  // initViewer zaten HTML'deki onload'dan \u00e7al\u0131\u015f\u0131yor;
  // viewer set edildikten sonra overlay ba\u015flats\u0131n
  const waitViewer = setInterval(() => {
    if (viewer) {
      clearInterval(waitViewer);
      initOasisOverlay();
    }
  }, 500);
  
  // TKGM cache ready event'ini dinle
  window.addEventListener('tkgm-cache-ready', (e) => {
    console.log(`[OASIS] TKGM cache hazır: ${e.detail.cachedTiles} tile cache'lendi`);
    // İsteğe bağlı: kullanıcıya bildirim göster
    const notification = document.createElement('div');
    notification.style.cssText = `
      position: fixed; top: 20px; right: 20px; z-index: 10000;
      background: rgba(89, 152, 197, 0.9); color: #fff;
      padding: 8px 12px; border-radius: 6px; font-size: 11px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    `;
    notification.textContent = `TKGM binalar cache'lendi (${e.detail.cachedTiles} tile)`;
    document.body.appendChild(notification);
    setTimeout(() => notification.remove(), 4000);
  });
});

// ============================================================
// SAĞ SIDEBAR — özet kutucuklar + mini harita
// ============================================================
function updateRightSidebar() {
  const reports  = _allReports  || [];
  const clusters = _allClusters || [];
  const raw  = reports.length;
  const proc = reports.filter(r => r.severity_score != null).length;
  const pct  = raw ? Math.round((proc / raw) * 100) : 0;

  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  set('rs-raw',  raw);
  set('rs-proc', proc);
  set('rs-pct',  `%${pct}`);
  const bar = document.getElementById('rs-pct-bar');
  if (bar) bar.style.width = pct + '%';

  const evMap = new Map();
  reports.forEach(r => {
    const t = (r.processed_disaster_type || r.event_define || 'Bilinmiyor').trim();
    if (!t) return;
    evMap.set(t, (evMap.get(t) || 0) + 1);
  });
  renderTypeRow('rs-event-types', evMap);

  const clMap = new Map();
  clusters.forEach(c => {
    const t = (c.event_type || 'Bilinmiyor').trim();
    if (!t) return;
    clMap.set(t, (clMap.get(t) || 0) + 1);
  });
  renderTypeRow('rs-cluster-types', clMap);

  drawMiniMap(reports);
}

function renderTypeRow(hostId, map) {
  const host = document.getElementById(hostId);
  if (!host) return;
  if (!map.size) {
    host.innerHTML = '<div class="rs-empty">Veri yok</div>';
    return;
  }
  const isCluster   = hostId === 'rs-cluster-types';
  const handler     = isCluster ? 'setClusterTypeFilter' : 'setEventTypeFilter';
  const activeVal   = isCluster ? _clusterTypeFilter : _eventTypeFilter;

  const sorted = [...map.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  const total  = [...map.values()].reduce((a, b) => a + b, 0);

  const allCell = `<div class="rs-type-cell rs-clickable ${activeVal === null ? 'rs-active' : ''}" onclick="${handler}(null)" title="Tümü">
      <div class="rs-type-val" style="color:var(--a)">${total}</div>
      <div class="rs-type-lbl">Tümü</div>
    </div>`;

  const cells = sorted.map(([t, c]) => {
    const ec    = getEventColor(t);
    const color = (ec && ec.bg) || '#5998C5';
    const label = (ec && ec.label) || t.toUpperCase();
    const safe  = t.replace(/'/g, "\\'");
    const active = activeVal === t ? 'rs-active' : '';
    return `<div class="rs-type-cell rs-clickable ${active}" title="${t}" onclick="${handler}('${safe}')">
      <div class="rs-type-val" style="color:${color}">${c}</div>
      <div class="rs-type-lbl">${label}</div>
    </div>`;
  }).join('');

  host.innerHTML = allCell + cells;
}

var _miniMapResizeBound = false;
var _miniMapState = null;
function drawMiniMap(reports) {
  const cv = document.getElementById('rs-minimap-canvas');
  if (!cv) return;
  if (!_miniMapResizeBound) {
    window.addEventListener('resize', () => drawMiniMap(_allReports || []));
    cv.addEventListener('click', _onMiniMapClick);
    cv.style.cursor = 'crosshair';
    _miniMapResizeBound = true;
  }

  const host = cv.parentElement;
  const dpr  = window.devicePixelRatio || 1;
  const W = host.clientWidth  | 0;
  const H = host.clientHeight | 0;
  cv.width  = W * dpr;
  cv.height = H * dpr;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#0a0e14';
  ctx.fillRect(0, 0, W, H);

  if (!_buildingCentroids || !_buildingCentroids.length) {
    ctx.fillStyle = '#6b7280';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Bina verisi yok', W / 2, H / 2);
    return;
  }

  const urg = new Map();
  (reports || []).forEach(r => {
    const lat = parseFloat(r.latitude);
    const lon = parseFloat(r.longitude);
    if (isNaN(lat) || isNaN(lon)) return;
    const level = getSeverityLevel(r.severity_score ?? null);
    if (!level) return;
    let nearby = _findBuildingsNear(lat, lon, 30);
    if (nearby.length === 0) nearby = _findBuildingsNear(lat, lon, 80).slice(0, 1);
    nearby.forEach(b => {
      const id  = b.feature.properties.id;
      const cur = urg.get(id);
      if (!cur || SEVERITY_ORDER[level] > SEVERITY_ORDER[cur]) urg.set(id, level);
    });
  });

  let minLon =  Infinity, maxLon = -Infinity, minLat =  Infinity, maxLat = -Infinity;
  _buildingCentroids.forEach(b => {
    if (b.lon < minLon) minLon = b.lon;
    if (b.lon > maxLon) maxLon = b.lon;
    if (b.lat < minLat) minLat = b.lat;
    if (b.lat > maxLat) maxLat = b.lat;
  });
  const padLon = (maxLon - minLon) * 0.04 || 0.001;
  const padLat = (maxLat - minLat) * 0.04 || 0.001;
  minLon -= padLon; maxLon += padLon;
  minLat -= padLat; maxLat += padLat;

  const cellPx = 9;
  const cols = Math.max(1, Math.floor(W / cellPx));
  const rows = Math.max(1, Math.floor(H / cellPx));
  const offX = (W - cols * cellPx) / 2;
  const offY = (H - rows * cellPx) / 2;

  const allCells = new Map();
  const urgCells = new Map();

  _buildingCentroids.forEach(b => {
    const x = (b.lon - minLon) / (maxLon - minLon) * cols;
    const y = (1 - (b.lat - minLat) / (maxLat - minLat)) * rows;
    const c = Math.min(cols - 1, Math.max(0, Math.floor(x)));
    const r = Math.min(rows - 1, Math.max(0, Math.floor(y)));
    const key = r + ',' + c;
    allCells.set(key, (allCells.get(key) || 0) + 1);
    const lvl = urg.get(b.feature.properties.id);
    if (lvl) {
      const ord = SEVERITY_ORDER[lvl];
      const cur = urgCells.get(key);
      if (!cur || ord > cur.ord) urgCells.set(key, { ord, level: lvl });
    }
  });

  ctx.globalAlpha = 0.35;
  ctx.fillStyle   = '#3b4655';
  allCells.forEach((cnt, key) => {
    if (urgCells.has(key)) return;
    const [r, c] = key.split(',').map(Number);
    ctx.fillRect(offX + c * cellPx + 1, offY + r * cellPx + 1, cellPx - 2, cellPx - 2);
  });

  ctx.globalAlpha = 0.92;
  urgCells.forEach(({ level }, key) => {
    const [r, c] = key.split(',').map(Number);
    ctx.fillStyle = SEVERITY_COLOR[level].hex;
    ctx.fillRect(offX + c * cellPx + 1, offY + r * cellPx + 1, cellPx - 2, cellPx - 2);
  });
  ctx.globalAlpha = 1;

  _miniMapState = { W, H, cellPx, cols, rows, offX, offY, minLon, maxLon, minLat, maxLat };
}

function _onMiniMapClick(ev) {
  if (!_miniMapState || !viewer) return;
  const cv = ev.currentTarget;
  const rect = cv.getBoundingClientRect();
  const x = ev.clientX - rect.left;
  const y = ev.clientY - rect.top;
  const { cellPx, cols, rows, offX, offY, minLon, maxLon, minLat, maxLat } = _miniMapState;
  const c = Math.floor((x - offX) / cellPx);
  const r = Math.floor((y - offY) / cellPx);
  if (c < 0 || c >= cols || r < 0 || r >= rows) return;
  const lon = minLon + ((c + 0.5) / cols) * (maxLon - minLon);
  const lat = minLat + (1 - (r + 0.5) / rows) * (maxLat - minLat);
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(lon, lat, 600),
    orientation: { heading: 0, pitch: Cesium.Math.toRadians(-65), roll: 0 },
    duration: 1.2,
  });
}

// ============================================================
// RAPOR DETAY MODAL (Sağ sidebar "Raporlar" > Detay butonu)
// ============================================================
function openReportsDetailModal() {
  const modal = document.getElementById('reportsDetailModal');
  if (!modal) return;
  renderReportsDetailModal();
  modal.classList.add('open');
}

function closeReportsDetailModal() {
  const modal = document.getElementById('reportsDetailModal');
  if (modal) modal.classList.remove('open');
}

function _rdmSevClass(sev) {
  if (sev == null) return '';
  if (sev >= 8) return 'high';
  if (sev >= 5) return 'mid';
  return 'low';
}

function _rdmCard(r, isProcessed) {
  const ec = getEventColor(r.processed_disaster_type || r.event_define);
  const borderColor = ec.bg;
  const label = (isProcessed ? (r.processed_disaster_type || ec.label) : (r.event_define || ec.label));
  const sev = r.severity_score;
  const sevHtml = (isProcessed && sev != null)
    ? `<span class="rdm-sev ${_rdmSevClass(sev)}">⚠ ${Number(sev).toFixed(1)}</span>` : '';

  const bhvr = r.user_behavior === 'victim' ? 'Afetzede' : r.user_behavior === 'observer' ? 'Gözlemci' : (r.user_behavior || '—');
  const uStatus = r.processed_user_status || '—';
  const loc = r.processed_location_match_score != null
    ? `%${Math.round(r.processed_location_match_score * 100)}`
    : (r.location_match_score != null ? `%${Math.round(r.location_match_score * 100)}` : '—');
  const coords = (r.latitude && r.longitude)
    ? `${Number(r.latitude).toFixed(4)}, ${Number(r.longitude).toFixed(4)}`
    : '—';

  const text = isProcessed
    ? (r.processed_summary || r.event_define || 'Açıklama yok')
    : (r.event_define || 'Açıklama yok');

  const metaItems = [
    `<span>ID <b>${r.id ?? '—'}</b></span>`,
    `<span>Davranış <b>${bhvr}</b></span>`,
    isProcessed ? `<span>Durum <b>${uStatus}</b></span>` : '',
    `<span>Konum <b>${loc}</b></span>`,
    `<span>Koord <b>${coords}</b></span>`,
  ].filter(Boolean).join('');

  return `<div class="rdm-card" style="--card-border:${borderColor}" onclick="onCardClick('${r.id}', ${parseFloat(r.longitude)}, ${parseFloat(r.latitude)})">
    <div class="rdm-card-top">
      <span class="rdm-card-label">${label}</span>
      <span style="display:flex;align-items:center;gap:6px">${sevHtml}<span class="rdm-card-time">${fmtDate(r.report_date)}</span></span>
    </div>
    <div class="rdm-card-meta">${metaItems}</div>
    <div class="rdm-card-text">${text}</div>
  </div>`;
}

function renderReportsDetailModal() {
  const rawList  = document.getElementById('rdm-raw-list');
  const procList = document.getElementById('rdm-proc-list');
  if (!rawList || !procList) return;

  const all = Array.isArray(_allReports) ? _allReports : [];
  const proc = all.filter(r => r.severity_score != null);
  const total = all.length;
  const pct = total ? Math.round((proc.length / total) * 100) : 0;

  const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setText('rdm-raw-count', total);
  setText('rdm-proc-count', proc.length);
  setText('rdm-pct', `%${pct}`);
  setText('rdm-raw-hdr-count',  `${total} kayıt`);
  setText('rdm-proc-hdr-count', `${proc.length} kayıt`);

  const byDateDesc = (a, b) => new Date(b.report_date || 0) - new Date(a.report_date || 0);
  const rawSorted  = [...all].sort(byDateDesc);
  const procSorted = [...proc].sort(byDateDesc);

  rawList.innerHTML  = rawSorted.length  ? rawSorted.map(r  => _rdmCard(r, false)).join('') : '<div class="rdm-empty">Ham rapor yok</div>';
  procList.innerHTML = procSorted.length ? procSorted.map(r => _rdmCard(r, true)).join('')  : '<div class="rdm-empty">İşlenmiş rapor yok</div>';
}

