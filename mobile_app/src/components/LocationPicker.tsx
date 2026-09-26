import { useState } from 'react';
import { CircleMarker, MapContainer, TileLayer, useMapEvents } from 'react-leaflet';
import type { LatLng } from '../lib/types';

interface Props {
  reason: string;
  initial: LatLng;
  onConfirm: (location: LatLng) => void;
  onCancel: () => void;
}

function ClickToPlace({ onPick }: { onPick: (l: LatLng) => void }) {
  useMapEvents({
    click: (e) => onPick({ latitude: e.latlng.lat, longitude: e.latlng.lng }),
  });
  return null;
}

/**
 * Fallback when the browser cannot provide a GPS fix
 * (permission denied, location off, or the page is not served over HTTPS).
 */
export function LocationPicker({ reason, initial, onConfirm, onCancel }: Props) {
  const [picked, setPicked] = useState<LatLng | null>(null);

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="picker-title">
      <div className="modal__card">
        <h2 id="picker-title" className="modal__title">Konumunu haritadan seç</h2>
        <p className="modal__text">{reason} Olay yerini haritaya dokunarak işaretleyebilirsin.</p>
        <div className="modal__map">
          <MapContainer center={[initial.latitude, initial.longitude]} zoom={6} className="fill">
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <ClickToPlace onPick={setPicked} />
            {picked && (
              <CircleMarker
                center={[picked.latitude, picked.longitude]}
                radius={10}
                pathOptions={{ color: '#fff', weight: 2, fillColor: '#D90000', fillOpacity: 1 }}
              />
            )}
          </MapContainer>
        </div>
        <div className="modal__actions">
          <button type="button" className="link-btn" onClick={onCancel}>
            Vazgeç
          </button>
          <button
            type="button"
            className="btn btn--small"
            disabled={!picked}
            onClick={() => picked && onConfirm(picked)}
          >
            Bu konumu kullan
          </button>
        </div>
      </div>
    </div>
  );
}
