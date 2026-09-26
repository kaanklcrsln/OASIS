import { BackButton } from '../components/Layout';
import { ReportsMap } from '../components/ReportsMap';
import type { LatLng, Report } from '../lib/types';

interface Props {
  reports: Report[];
  fallbackCenter: LatLng;
  onBack: () => void;
  onDelete: (report: Report) => void;
}

export function MyRequests({ reports, fallbackCenter, onBack, onDelete }: Props) {
  const pending = reports.filter((r) => !r.synced).length;

  return (
    <div className="screen">
      <div className="map-pane">
        <ReportsMap reports={reports} fallbackCenter={fallbackCenter} />
        <div className="map-header">
          <BackButton onClick={onBack} elevated />
        </div>
      </div>
      <section className="list-pane">
        <h2 className="list-title">
          Talep Listesi ({reports.length})
          {pending > 0 && <span className="pending"> 🔄 {pending} bekliyor</span>}
        </h2>
        {reports.length === 0 ? (
          <p className="muted center-text">Henüz bir talep yok.</p>
        ) : (
          <ul className="card-list">
            {[...reports].reverse().map((r) => (
              <li key={r.id} className="card">
                {r.thumbnail ? (
                  <img src={r.thumbnail} alt="" className="card-img" />
                ) : (
                  <div className="card-img card-img--empty">📷</div>
                )}
                <div className="card-body">
                  <div className="card-row">
                    <span className="card-id">{r.report_id}</span>
                    <span title={r.synced ? 'Gönderildi' : 'Gönderilmeyi bekliyor'}>{r.synced ? '✅' : '🔄'}</span>
                  </div>
                  <div className="card-text">{r.event_define || 'Detay girilmedi'}</div>
                  <div className="card-date">{new Date(r.report_date).toLocaleString('tr-TR')}</div>
                </div>
                <button type="button" className="del-btn" onClick={() => onDelete(r)}>
                  SİL
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
