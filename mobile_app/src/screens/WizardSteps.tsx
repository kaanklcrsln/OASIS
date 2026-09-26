import { useRef, useState } from 'react';
import { Btn, Layout, NextBtn } from '../components/Layout';
import { sanitizeInput } from '../lib/text';

export function WhoNeedsHelp({ onBack, onMe, onSomeoneElse }: {
  onBack: () => void;
  onMe: () => void;
  onSomeoneElse: () => void;
}) {
  return (
    <Layout onBack={onBack}>
      <div className="content">
        <h1 className="title">Kimin yardıma ihtiyacı var?</h1>
        <Btn title="Benim" onClick={onMe} />
        <div className="spacer" />
        <Btn title="Bir başkasının" onClick={onSomeoneElse} />
      </div>
    </Layout>
  );
}

export function CanYouExplain({ onBack, onYes, onEmergency }: {
  onBack: () => void;
  onYes: () => void;
  onEmergency: () => void;
}) {
  return (
    <Layout onBack={onBack}>
      <div className="content">
        <h1 className="title">Durumu anlatabilecek durumda mısın?</h1>
        <Btn title="Evet" onClick={onYes} />
        <div className="spacer" />
        <Btn title="Hayır, Acil Yardım!" red onClick={onEmergency} />
      </div>
    </Layout>
  );
}

export function Chatbot({ initialText, onBack, onNext }: {
  initialText: string;
  onBack: () => void;
  onNext: (text: string) => void;
}) {
  const [text, setText] = useState(initialText);

  return (
    <Layout onBack={onBack}>
      <div className="content">
        <h1 className="title">Durumu anlat</h1>
        <textarea
          className="input-box"
          placeholder="Ne olduğunu açıkla..."
          value={text}
          onChange={(e) => setText(sanitizeInput(e.target.value))}
          autoFocus
          rows={3}
        />
        <div className="next-box">
          <NextBtn onClick={() => onNext(text)} />
        </div>
      </div>
    </Layout>
  );
}

export function PhotoUpload({ photoUrl, onBack, onPhoto, onNext }: {
  photoUrl: string | null;
  onBack: () => void;
  onPhoto: (file: File) => void;
  onNext: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <Layout onBack={onBack} fullBleed>
      <div className="photo-screen">
        <div className="cam-area">
          {photoUrl ? (
            <img src={photoUrl} alt="Çekilen fotoğraf" className="cam-preview" onClick={() => inputRef.current?.click()} />
          ) : (
            <>
              <button type="button" className="cam-btn" onClick={() => inputRef.current?.click()} aria-label="Fotoğraf çek">
                📷
              </button>
              <p className="cam-hint">
                Fotoğraf çekmek için kameraya dokun
                <br />
                (İsteğe Bağlı)
              </p>
            </>
          )}
          {/* `capture` opens the rear camera on phones; desktops fall back to a file picker */}
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            capture="environment"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onPhoto(file);
              e.target.value = '';
            }}
          />
        </div>
        <div className="bottom-area">
          <button type="button" className="link-btn" onClick={onNext}>
            {photoUrl ? 'Devam Et' : 'Fotoğrafsız Devam Et'}
          </button>
          <NextBtn onClick={onNext} />
        </div>
      </div>
    </Layout>
  );
}

export function ConfirmReport({ photoUrl, text, loading, onBack, onSubmit }: {
  photoUrl: string | null;
  text: string;
  loading: boolean;
  onBack: () => void;
  onSubmit: () => void;
}) {
  return (
    <Layout onBack={loading ? undefined : onBack}>
      <div className="content">
        {photoUrl ? (
          <img src={photoUrl} alt="Önizleme" className="preview" />
        ) : (
          <div className="preview preview--empty">Fotoğraf Yok</div>
        )}
        <div className="summary-box">{text || 'Acil yardım talebi (Detay girilmedi).'}</div>
        <div className="spacer-lg" />
        {loading ? <Spinner label="Konum alınıyor..." /> : <Btn title="BİLDİR" red onClick={onSubmit} />}
      </div>
    </Layout>
  );
}

export function Success({ synced, onHome, onMap }: { synced: boolean; onHome: () => void; onMap: () => void }) {
  return (
    <Layout>
      <div className="content">
        <div className="success-icon">{synced ? '✅' : '📶'}</div>
        <h1 className="title title--tight">{synced ? 'Durum bildirilmiştir.' : 'Çevrimdışı kaydedildi.'}</h1>
        <p className="muted center-text">
          {synced
            ? 'Talebiniz sisteme kaydedildi ve haritaya işlendi.'
            : 'Şu an sunucuya ulaşılamıyor. Bağlantı geldiğinde otomatik olarak gönderilecek.'}
        </p>
        <div className="spacer-lg" />
        <Btn title="Ana Sayfaya Dön" onClick={onHome} />
        <div className="spacer" />
        <button type="button" className="link-btn link-btn--lg" onClick={onMap}>
          Haritada Gör
        </button>
      </div>
    </Layout>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="spinner-wrap">
      <div className="spinner" aria-hidden />
      {label && <p className="muted">{label}</p>}
    </div>
  );
}
