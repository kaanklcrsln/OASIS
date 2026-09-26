import type { ReactNode } from 'react';

interface LayoutProps {
  children: ReactNode;
  onBack?: () => void;
  /** Content fills the screen; back button floats on top. */
  fullBleed?: boolean;
}

export function Layout({ children, onBack, fullBleed }: LayoutProps) {
  return (
    <div className="screen screen--cream">
      {!fullBleed && (
        <header className="header">
          {onBack && <BackButton onClick={onBack} />}
        </header>
      )}
      {fullBleed && onBack && (
        <header className="header header--floating">
          <BackButton onClick={onBack} />
        </header>
      )}
      <main className="flex1">{children}</main>
    </div>
  );
}

export function BackButton({ onClick, elevated }: { onClick: () => void; elevated?: boolean }) {
  return (
    <button
      type="button"
      className={elevated ? 'back-circle' : 'back-btn'}
      onClick={onClick}
      aria-label="Geri"
    >
      ←
    </button>
  );
}

export function Btn({ title, onClick, red }: { title: string; onClick: () => void; red?: boolean }) {
  return (
    <button type="button" className={`btn${red ? ' btn--red' : ''}`} onClick={onClick}>
      {title}
    </button>
  );
}

export function NextBtn({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="next-btn" onClick={onClick} aria-label="İleri">
      →
    </button>
  );
}
