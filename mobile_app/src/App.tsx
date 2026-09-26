import { useCallback, useEffect, useRef, useState } from 'react';
import { ClusterBanner } from './components/ClusterBanner';
import { LocationPicker } from './components/LocationPicker';
import { Toast } from './components/Toast';
import * as api from './lib/api';
import { getCurrentLocation, LocationError } from './lib/geo';
import { readImage } from './lib/image';
import { loadReports, saveReports } from './lib/storage';
import { generateReportId } from './lib/text';
import type { Draft, LatLng, NearbyCluster, Report, ScreenName } from './lib/types';
import { Landing } from './screens/Landing';
import { MyRequests } from './screens/MyRequests';
import {
  CanYouExplain,
  Chatbot,
  ConfirmReport,
  PhotoUpload,
  Spinner,
  Success,
  WhoNeedsHelp,
} from './screens/WizardSteps';

const DEFAULT_CENTER: LatLng = { latitude: 39.9334, longitude: 32.8597 }; // Ankara
const SYNC_INTERVAL_MS = 30_000;
const BANNER_MS = 5_000;

const emptyDraft = (): Draft => ({
  victim: '',
  event_define: '',
  photo: null,
  photoUrl: null,
  photoInfo: null,
});

const BACK_ROUTES: Partial<Record<ScreenName, ScreenName>> = {
  MyRequests: 'Landing',
  WhoNeedsHelp: 'Landing',
  CanYouExplain: 'WhoNeedsHelp',
  PhotoUpload: 'Chatbot',
  ConfirmReport: 'PhotoUpload',
  Success: 'Landing',
};

export default function App() {
  const [screen, setScreen] = useState<ScreenName>('Landing');
  const [loading, setLoading] = useState(false);
  const [reports, setReports] = useState<Report[]>(loadReports);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [lastSynced, setLastSynced] = useState(true);
  const [cluster, setCluster] = useState<NearbyCluster | null>(null);
  const [clusterLeaving, setClusterLeaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [picker, setPicker] = useState<{ reason: string; canCommunicate: boolean } | null>(null);

  const reportsRef = useRef(reports);
  const syncingRef = useRef(false);
  const bannerTimer = useRef<number>();

  // Keep the ref current and persist every change
  useEffect(() => {
    reportsRef.current = reports;
    saveReports(reports);
  }, [reports]);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 4000);
  }, []);

  const dismissCluster = useCallback(() => {
    setClusterLeaving(true);
    window.setTimeout(() => {
      setCluster(null);
      setClusterLeaving(false);
    }, 300);
  }, []);

  const showCluster = useCallback(
    (c: NearbyCluster) => {
      setClusterLeaving(false);
      setCluster(c);
      window.clearTimeout(bannerTimer.current);
      bannerTimer.current = window.setTimeout(dismissCluster, BANNER_MS);
    },
    [dismissCluster],
  );

  // ── Offline queue: push unsynced reports when the API is reachable ─────────
  const syncPending = useCallback(async () => {
    if (syncingRef.current) return;
    const unsynced = reportsRef.current.filter((r) => !r.synced);
    if (unsynced.length === 0) return;
    syncingRef.current = true;

    const done = new Map<string, api.CreateReportResponse>();
    for (const r of unsynced) {
      try {
        const res = await api.createReport({
          report_id: r.report_id,
          user_behavior: r.user_behavior,
          can_communicate: r.can_communicate,
          latitude: r.gps_location.latitude,
          longitude: r.gps_location.longitude,
          event_define: r.event_define || null,
          event_capture: null,
          report_date: r.report_date,
        });
        done.set(r.id, res);
      } catch {
        // API still unreachable — retried on the next interval
      }
    }
    syncingRef.current = false;

    if (done.size > 0) {
      setReports((prev) =>
        prev.map((r) => {
          const res = done.get(r.id);
          if (!res) return r;
          return {
            ...r,
            id: res.id,
            synced: true,
            isInCluster: !!res.nearby_cluster,
            cluster_id: res.nearby_cluster?.cluster_id,
          };
        }),
      );
      showToast(`🔄 ${done.size} bekleyen rapor veritabanına yüklendi!`);
    }
  }, [showToast]);

  useEffect(() => {
    void syncPending();
    const id = window.setInterval(() => void syncPending(), SYNC_INTERVAL_MS);
    const onOnline = () => void syncPending();
    window.addEventListener('online', onOnline);
    return () => {
      window.clearInterval(id);
      window.removeEventListener('online', onOnline);
    };
  }, [syncPending]);

  // ── Draft helpers ──────────────────────────────────────────────────────────
  const resetDraft = () => {
    setDraft((d) => {
      if (d.photoUrl) URL.revokeObjectURL(d.photoUrl);
      return emptyDraft();
    });
  };

  const handlePhoto = async (file: File) => {
    try {
      const info = await readImage(file);
      setDraft((d) => {
        if (d.photoUrl) URL.revokeObjectURL(d.photoUrl);
        return { ...d, photo: file, photoUrl: URL.createObjectURL(file), photoInfo: info };
      });
    } catch {
      showToast('Fotoğraf okunamadı, lütfen tekrar deneyin.');
    }
  };

  // ── Submit ─────────────────────────────────────────────────────────────────
  // canCommunicate=false when the user pressed "Hayır, Acil Yardım!"
  const submitReport = async (canCommunicate: boolean, text: string, pickedLocation?: LatLng) => {
    if (loading) return;
    setLoading(true);

    let location: LatLng;
    try {
      location = pickedLocation ?? (await getCurrentLocation());
    } catch (e) {
      setLoading(false);
      const reason = e instanceof LocationError ? e.message : 'Konum alınamadı.';
      setPicker({ reason, canCommunicate });
      return;
    }

    const payload: api.CreateReportPayload = {
      report_id: generateReportId(),
      user_behavior: draft.victim === 'Me' ? 'victim' : 'observer',
      can_communicate: canCommunicate,
      latitude: location.latitude,
      longitude: location.longitude,
      event_define: text || null,
      event_capture: null,
      report_date: new Date().toISOString(),
    };

    let serverId: string | null = null;
    let detected: NearbyCluster | null = null;
    try {
      const created = await api.createReport(payload);
      serverId = created.id;
      detected = created.nearby_cluster;
      if (detected) showCluster(detected);

      if (draft.photo) {
        try {
          const uploaded = await api.uploadImage(created.report_id, draft.photo);
          // No GPS in the photo's EXIF → send the device position instead
          if (uploaded.exif?.gps?.latitude == null) {
            await api.uploadExif(created.report_id, {
              location,
              width: draft.photoInfo?.width,
              height: draft.photoInfo?.height,
              takenAt: new Date(draft.photo.lastModified).toISOString(),
            });
          }
        } catch (err) {
          console.warn('Fotoğraf yüklenemedi:', err);
        }
      }
    } catch (err) {
      // API down or offline — kept locally and synced later
      console.warn('API bağlantısı yok, çevrimdışı kaydediliyor:', err);
    }

    const newReport: Report = {
      id: serverId ?? `local-${crypto.randomUUID?.() ?? Date.now()}`,
      report_id: payload.report_id,
      user_behavior: payload.user_behavior,
      can_communicate: canCommunicate,
      event_define: text,
      thumbnail: draft.photoInfo?.thumbnail ?? null,
      gps_location: location,
      report_date: payload.report_date,
      synced: serverId !== null,
      isInCluster: detected !== null,
      cluster_id: detected?.cluster_id,
    };
    setReports((prev) => [...prev, newReport]);
    setLastSynced(newReport.synced);
    setLoading(false);
    setScreen('Success');
  };

  const handleDelete = async (report: Report) => {
    if (!window.confirm('Bu talebi kaldırmak istediğine emin misin?')) return;
    if (report.synced) {
      try {
        await api.deleteReport(report.id);
      } catch (e) {
        console.warn('API silme hatası:', e);
      }
    }
    setReports((prev) => prev.filter((r) => r.id !== report.id));
  };

  // ── Navigation ─────────────────────────────────────────────────────────────
  const back = () => {
    if (screen === 'Chatbot') setScreen(draft.victim === 'Me' ? 'CanYouExplain' : 'WhoNeedsHelp');
    else setScreen(BACK_ROUTES[screen] ?? 'Landing');
  };

  const lastLocation = reports[reports.length - 1]?.gps_location ?? DEFAULT_CENTER;

  return (
    <div className="app-shell">
      {screen === 'Landing' && (
        <Landing
          onReport={() => {
            resetDraft();
            setScreen('WhoNeedsHelp');
          }}
          onMyRequests={() => setScreen('MyRequests')}
        />
      )}
      {screen === 'MyRequests' && (
        <MyRequests reports={reports} fallbackCenter={lastLocation} onBack={back} onDelete={handleDelete} />
      )}
      {screen === 'WhoNeedsHelp' && (
        <WhoNeedsHelp
          onBack={back}
          onMe={() => {
            setDraft((d) => ({ ...d, victim: 'Me' }));
            setScreen('CanYouExplain');
          }}
          onSomeoneElse={() => {
            setDraft((d) => ({ ...d, victim: 'SomeoneElse' }));
            setScreen('Chatbot');
          }}
        />
      )}
      {screen === 'CanYouExplain' && (
        <CanYouExplain onBack={back} onYes={() => setScreen('Chatbot')} onEmergency={() => submitReport(false, '')} />
      )}
      {screen === 'Chatbot' && (
        <Chatbot
          initialText={draft.event_define}
          onBack={back}
          onNext={(text) => {
            setDraft((d) => ({ ...d, event_define: text }));
            if (draft.victim === 'SomeoneElse') setScreen('PhotoUpload');
            else void submitReport(true, text);
          }}
        />
      )}
      {screen === 'PhotoUpload' && (
        <PhotoUpload
          photoUrl={draft.photoUrl}
          onBack={back}
          onPhoto={handlePhoto}
          onNext={() => setScreen('ConfirmReport')}
        />
      )}
      {screen === 'ConfirmReport' && (
        <ConfirmReport
          photoUrl={draft.photoUrl}
          text={draft.event_define}
          loading={loading}
          onBack={back}
          onSubmit={() => submitReport(true, draft.event_define)}
        />
      )}
      {screen === 'Success' && (
        <Success
          synced={lastSynced}
          onHome={() => {
            resetDraft();
            setScreen('Landing');
          }}
          onMap={() => setScreen('MyRequests')}
        />
      )}

      {loading && screen !== 'ConfirmReport' && (
        <div className="overlay">
          <Spinner label="Konum alınıyor..." />
        </div>
      )}

      {picker && (
        <LocationPicker
          reason={picker.reason}
          initial={lastLocation}
          onCancel={() => setPicker(null)}
          onConfirm={(loc) => {
            const { canCommunicate } = picker;
            setPicker(null);
            void submitReport(canCommunicate, draft.event_define, loc);
          }}
        />
      )}

      {cluster && <ClusterBanner cluster={cluster} leaving={clusterLeaving} onClose={dismissCluster} />}
      {toast && <Toast message={toast} />}
    </div>
  );
}
