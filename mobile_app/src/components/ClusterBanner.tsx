import type { NearbyCluster } from '../lib/types';

interface Props {
  cluster: NearbyCluster;
  leaving: boolean;
  onClose: () => void;
}

/** Yellow warning shown when the new report falls inside an existing disaster cluster. */
export function ClusterBanner({ cluster, leaving, onClose }: Props) {
  return (
    <div className={`cluster-banner${leaving ? ' cluster-banner--leaving' : ''}`} role="alert">
      <div className="flex1">
        <div className="cluster-banner__title">⚠️ Afet Kümesi Tespit Edildi</div>
        <div className="cluster-banner__msg">{cluster.message}</div>
        {cluster.event_type && <div className="cluster-banner__sub">Tür: {cluster.event_type}</div>}
      </div>
      <button type="button" className="cluster-banner__close" onClick={onClose} aria-label="Kapat">
        ✕
      </button>
    </div>
  );
}
