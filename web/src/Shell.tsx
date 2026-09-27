export function ProductHeader({ current }: { current: 'workspace' | 'library' | 'join' }) {
  return <header className="topbar product-topbar"><a className="brand" href="#/capture" aria-label="Counterpart home"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M4 7h10v18H4zM18 7h10v18H18zM11 16h10m-4-4 4 4-4 4" fill="none" stroke="currentColor" strokeWidth="2"/></svg>counterpart<span className="brand-period">.</span></a><span className="topbar-caption">Better examples.<br/>Built together.</span><nav className="product-nav" aria-label="Primary"><a href="#/capture">Capture</a><a href="#/library" aria-current={current === 'library' ? 'page' : undefined}>Your sessions</a><a href="#/workspace" aria-current={current === 'workspace' ? 'page' : undefined}>Workspace</a></nav></header>;
}

export function Alert({ message, dismiss }: { message: string | null; dismiss?: () => void }) {
  return message ? <div className="global-message error" role="alert"><span>!</span><p>{message}</p>{dismiss && <button onClick={dismiss} aria-label="Dismiss error">×</button>}</div> : null;
}

