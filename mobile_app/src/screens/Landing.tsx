interface Props {
  onReport: () => void;
  onMyRequests: () => void;
}

export function Landing({ onReport, onMyRequests }: Props) {
  return (
    <div className="screen screen--blue center">
      <img src="/icon-192.png" alt="OASIS" className="landing-logo" />
      <button type="button" className="white-btn" onClick={onReport}>
        Afet bildir
      </button>
      <div className="spacer" />
      <button type="button" className="trans-btn" onClick={onMyRequests}>
        Taleplerim / Harita
      </button>
    </div>
  );
}
