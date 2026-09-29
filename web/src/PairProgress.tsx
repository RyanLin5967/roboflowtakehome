import type { Capture, Connection, Session } from './api';
import { CaptureMedia } from './Evidence';
import { appearanceNeedsReview } from './captureGuidance';
import './pair-progress.css';

type PairProgressProps = {
  session: Session;
  connection: Connection;
  baseline?: Capture | null;
  counterpart?: Capture | null;
  onRetakeFirst: () => void;
  onRetakeSecond?: () => void;
  canRetakeFirst?: boolean;
  canRetakeSecond?: boolean;
  disabled?: boolean;
  resetPending?: 'baseline' | 'counterpart' | null;
};

const readable = (value: string) => value.replaceAll('_', ' ');
const capitalize = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);

/** The cup/reference stays at x=80 in both illustrations; only object A moves. */
function SpatialSetup({ right, objectA, objectB }: { right: boolean; objectA: string; objectB: string }) {
  const movingX = right ? 128 : 31;
  return <svg className="pair-progress-setup" viewBox="0 0 160 96" role="img" aria-label={`Illustration: ${readable(objectA)} ${right ? 'right' : 'left'} of ${readable(objectB)}. The ${readable(objectB)} stays in place.`}>
    <path d="M8 76H152" stroke="#cccbbd" strokeWidth="2" />
    <path d="M80 15V82" stroke="#d9d8cb" strokeDasharray="3 4" />
    {objectB === 'cup' ? <g fill="#ede8d7" stroke="#68745d" strokeWidth="2.5" strokeLinejoin="round">
      <path d="M66 42H92L89 74H69Z" />
      <path d="M92 47H99C111 47 111 64 96 64H91" fill="none" />
      <path d="M69 43H89" stroke="#9d9f8c" />
    </g> : <g><rect x="65" y="43" width="30" height="31" rx="5" fill="#e7ebde" stroke="#68745d" strokeWidth="2" /><text x="80" y="65" textAnchor="middle" fill="#49634c" fontSize="20" fontWeight="600">B</text></g>}
    {objectA === 'bottle' ? <g transform={`translate(${movingX}, 0)`} fill="#d39874" stroke="#884329" strokeWidth="2.5" strokeLinejoin="round">
      <path d="M-6 24H6V34L11 42V72Q11 75 7 75H-7Q-11 75-11 72V42L-6 34Z" />
      <path d="M-7 21H7V27H-7Z" fill="#884329" />
      <path d="M-8 50H8V62H-8Z" fill="#f4dfc6" stroke="none" />
    </g> : <g><rect x={movingX - 14} y="40" width="28" height="34" rx="7" fill="#d39874" stroke="#884329" strokeWidth="2" /><text x={movingX} y="64" textAnchor="middle" fill="#693322" fontSize="20" fontWeight="600">A</text></g>}
  </svg>;
}

function ArrivalSetup({ first, second }: { first: string; second: string }) {
  return <ol className="pair-progress-order" aria-label="Arrival order">
    <li><span>1</span><span>{readable(first)}</span></li>
    <li><span>2</span><span>{readable(second)}</span></li>
  </ol>;
}

export function PairProgress({ session, connection, baseline = session.baseline, counterpart = session.counterpart, onRetakeFirst, onRetakeSecond, canRetakeFirst = Boolean(session.baseline || session.latest_capture), canRetakeSecond = Boolean(session.counterpart), disabled = false, resetPending = null }: PairProgressProps) {
  const spatial = session.recipe === 'spatial';
  const published = session.state === 'published';
  const humanReview = Boolean(session.human_review_required || session.baseline_authorization);
  const objectA = readable(session.object_a);
  const objectB = readable(session.object_b);
  const saved = [baseline, counterpart];

  return <section className="pair-progress" aria-label={spatial ? 'Your two photos' : 'Your two recordings'}>
    {saved.map((capture, index) => {
      const first = index === 0;
      const slot = first ? 'baseline' : 'counterpart';
      const attempt = session.latest_capture?.slot === slot ? session.latest_capture : null;
      const shownCapture = capture || attempt;
      const checking = !capture && (attempt?.status === 'queued' || attempt?.status === 'running');
      const current = !published && !capture && (first || Boolean(baseline));
      const retry = current && attempt && !checking;
      const reviewAttempt = !published && !first && !capture && appearanceNeedsReview(attempt?.result, attempt);
      const review = Boolean(capture && humanReview && !published);
      const status = published || capture ? review ? 'Saved for review' : 'Saved' : reviewAttempt ? 'Needs review' : checking ? 'Checking…' : retry ? 'Try again' : current ? first ? 'Do this first' : 'Do this now' : 'Then do this';
      const statusLabel = `${spatial ? 'Photo' : 'Recording'} ${index + 1} ${status.toLowerCase()}`;
      const state = review || reviewAttempt ? 'review' : capture || published ? 'saved' : current ? 'current' : 'next';
      const resetThis = resetPending === slot;
      const canRetake = first ? canRetakeFirst : canRetakeSecond;
      const onRetake = first ? onRetakeFirst : onRetakeSecond;
      const actionLabel = resetThis ? 'Retry reset' : `Retake ${first ? 'first' : 'second'} ${spatial ? 'image' : 'sequence'}`;
      return <article key={slot} className={`pair-progress-card ${state}`} aria-label={statusLabel} aria-current={current ? 'step' : undefined}>
        <div className="pair-progress-heading"><span>{spatial ? 'Photo' : 'Recording'} {index + 1}</span><span className="pair-progress-status">{(capture || published) && <span aria-hidden="true">✓ </span>}{status}</span></div>
        <div className="pair-progress-body">
          <div className="pair-progress-thumbnail">
            {shownCapture?.frames.length ? <CaptureMedia capture={shownCapture} connection={connection} labels={[session.object_a, session.object_b]} small /> : spatial ? <SpatialSetup right={!first} objectA={session.object_a} objectB={session.object_b} /> : <ArrivalSetup first={first ? objectA : objectB} second={first ? objectB : objectA} />}
          </div>
          <h3>{spatial ? <>{capitalize(objectA)} <strong>{first ? 'LEFT' : 'RIGHT'}</strong> of {objectB}</> : <>{capitalize(first ? objectA : objectB)} <strong>arrives first</strong></>}</h3>
        </div>
        {!published && canRetake && onRetake && <button className="pair-progress-retake" onClick={onRetake} disabled={disabled || (resetPending !== null && !resetThis)}>{actionLabel}</button>}
        {!published && first && counterpart && <span className="pair-progress-retake-note">Retaking {spatial ? 'photo 1' : 'recording 1'} starts the pair over.</span>}
      </article>;
    })}
  </section>;
}
