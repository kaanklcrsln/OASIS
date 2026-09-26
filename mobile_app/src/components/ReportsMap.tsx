import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet';
import type { LatLng, Report } from '../lib/types';

interface Props {
  reports: Report[];
  fallbackCenter: LatLng;
}

export function ReportsMap({ reports, fallbackCenter }: Props) {
  const last = reports[reports.length - 1];
  const center = last ? last.gps_location : fallbackCenter;

  return (
    <MapContainer center={[center.latitude, center.longitude]} zoom={13} className="fill" zoomControl={false}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {reports.map((r) => (
        <CircleMarker
          key={r.id}
          center={[r.gps_location.latitude, r.gps_location.longitude]}
          radius={9}
          pathOptions={{
            color: '#fff',
            weight: 2,
            fillColor: r.isInCluster ? '#007AFF' : '#D90000',
            fillOpacity: 1,
          }}
        >
          <Popup>
            <strong>{r.event_define || 'Afet bildirimi'}</strong>
            {r.isInCluster && <div>⚠️ Afet kümesi içinde</div>}
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
