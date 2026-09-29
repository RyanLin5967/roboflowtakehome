import RecordedChecks, { checkOutcome } from './RecordedChecks';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, clearPending, download, ensureHistoryCapacity, loadConnection, readPending, rememberSession, request, saveConnection, savePending, submitPending } from './api';
import type { Capture, Connection, Mode, PendingCapture, Recipe, Result, Scenario, Session, Slot } from './api';
import { useCamera } from './camera';
import { CaptureMedia } from './Evidence';
import { registrationGuidance } from './captureGuidance';
import { PairProgress } from './PairProgress';
import './capture-flow.css';

const titles: Record<Recipe, string> = { spatial: 'Switch sides', temporal: 'Reverse arrival order' };
const pendingStatus = (capture: Capture | null | undefined) => !!capture && ['queued', 'running'].includes(capture.status);
const humanize = (text: string) => text.replaceAll('_', ' ').replace(/^./, value => value.toUpperCase());
const configurationErrors: Record<string, string> = { execution_contract_mismatch: 'The worker uses a different detector than this collection requires. The operator must restore the pinned configuration.', execution_contract_migration_required: 'This historical session needs its recorded model pinned by the operator before inference can continue.', execution_evidence_mismatch: 'The saved observations do not match the frozen model contract. The operator must inspect their provenance.', detector_configuration_changed: 'The worker detector changed after startup. The operator must restart it with the pinned configuration.', provider_model_mismatch: 'Roboflow returned a different model identity. The response was not accepted; the operator must inspect the provider configuration.' };
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';

function Logo() { return <span className="brand"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M4 7h10v18H4zM18 7h10v18H18z" fill="none" stroke="currentColor" strokeWidth="2"/><path d="M11 16h10m-4-4 4 4-4 4" fill="none" stroke="currentColor" strokeWidth="2"/></svg>counterpart<span className="brand-period">.</span></span>; }
function Arrow() { return <span aria-hidden="true">↗</span>; }
function SetupDiagram({ temporal = false, objectA = 'bottle', objectB = 'cup' }: { temporal?: boolean; objectA?: string; objectB?: string }) {
  if (objectA !== 'bottle' || objectB !== 'cup') return <svg className="setup-diagram" viewBox="0 0 720 440" role="img" aria-label={`Setup guide for ${objectA} and ${objectB}`}><rect width="720" height="440" fill="#ece9df"/>{temporal && <rect x="446" width="274" height="440" fill="#dbe5d5"/>}<rect x="115" y="190" width="130" height="135" rx="5" fill="#dfbfa5" stroke="#ad603e"/><text x="180" y="265" textAnchor="middle" fontSize="20" fill="#72472e">{objectA}</text><rect x={temporal ? 130 : 325} y={temporal ? 45 : 190} width="150" height="120" rx="5" fill="#e0e5d4" stroke="#738365"/><text x={temporal ? 205 : 400} y={temporal ? 110 : 255} textAnchor="middle" fontSize="20" fill="#485c41">{objectB}</text><path d={temporal ? 'M300 270h300m-20-13 20 13-20 13M320 100h280m-20-13 20 13-20 13' : 'M215 145q165-110 350 40m-24-4 24 5-7-24'} stroke="#ad603e" strokeWidth="3" strokeDasharray="7 5" fill="none"/><text x="28" y="411" fill="#736e62" fontSize="13" fontFamily="monospace">SETUP GUIDE · NOT MODEL PREDICTIONS</text></svg>;
  return <svg className="setup-diagram" viewBox="0 0 720 440" role="img" aria-label={temporal ? 'Setup guide: cup and bottle begin on the left and move into the right target band in sequence.' : 'Setup guide: move the bottle from the left of the stationary cup to its right.'}>
    <defs><pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#d3cfc3"/></pattern></defs>
    <rect width="720" height="440" fill="#ece9df"/><rect width="720" height="440" fill="url(#grid)"/>
    {temporal && <><rect x="446" width="274" height="440" fill="#dbe5d5" opacity=".7"/><path d="M446 0v440" stroke="#658064" strokeWidth="2" strokeDasharray="8 8"/><text x="472" y="38" fill="#536953" fontSize="14" fontFamily="monospace">TARGET AREA</text></>}
    <ellipse cx="210" cy={temporal ? 296 : 334} rx="62" ry="12" fill="#d5d0c4"/><path d={temporal ? 'M181 163v20l-15 25v81q0 10 10 10h70q10 0 10-10v-81l-15-25v-20z' : 'M181 163v20l-15 25v116q0 10 10 10h70q10 0 10-10V208l-15-25v-20z'} fill="#ad603e"/>
    <rect x="180" y="151" width="62" height="18" rx="3" fill="#543e31"/><rect x="168" y="235" width="86" height="45" fill="#ead9bd"/><path d="M195 246h34m-34 12h24" stroke="#b7926d" strokeWidth="3"/>
    <g transform={temporal ? 'translate(-170,-120)' : ''}><ellipse cx="397" cy="333" rx="62" ry="12" fill="#d5d0c4"/><path d="M345 235h96l-8 83q-36 29-79 0z" fill="#faf8ee" stroke="#7d8371" strokeWidth="3"/><path d="M440 251h19q24 28-22 43" fill="none" stroke="#7d8371" strokeWidth="9"/><ellipse cx="393" cy="235" rx="48" ry="12" fill="#c3b394" stroke="#7d8371" strokeWidth="3"/></g>
    {temporal ? <><path d="M294 124h265m-14-12 14 12-14 12M294 280h265m-14-12 14 12-14 12" stroke="#aa5738" strokeWidth="3" fill="none" strokeDasharray="7 5"/></> : <><path d="M241 111q185-116 351 52m-20-3 22 7-3-23" stroke="#aa5738" strokeWidth="3" fill="none" strokeDasharray="7 5"/><path d="M556 213v15l-11 19v73q0 8 8 8h53q8 0 8-8v-73l-11-19v-15z" fill="none" stroke="#aa5738" strokeWidth="2" strokeDasharray="7 5"/></>}
    <text x="28" y="411" fill="#736e62" fontSize="13" fontFamily="monospace">SETUP GUIDE · NOT MODEL PREDICTIONS</text>
  </svg>;
}

function ReferenceGhost({ session, width, height }: { session: Session; width: number; height: number }) {
  const frame = session.baseline?.frames[0];
  const candidates = frame?.detections?.filter(item => item.label === session.object_b && item.confidence >= .6) || [];
  if (!frame || candidates.length !== 1 || !width || !height || Math.abs(width / height / (frame.width / frame.height) - 1) > .02) return null;
  const box = candidates[0].box;
  return <svg className="ghost-reference" viewBox={`0 0 ${width} ${height}`} aria-label={`Saved ${session.object_b} position guide`}><rect x={box[0] * width} y={box[1] * height} width={(box[2] - box[0]) * width} height={(box[3] - box[1]) * height} fill="none" strokeWidth="2" vectorEffect="non-scaling-stroke"/><text x={box[0] * width + 5} y={Math.max(20, box[1] * height - 10)}>Saved {session.object_b} position</text></svg>;
}

function Checks({ result }: { result?: Result | null }) {
  if (!result?.checks?.length) return null;
  const unique = [...new Map(result.checks.map(check => [`${check.name}:${checkOutcome(check)}`, check])).values()];
  return <details className="check-details"><summary>View all {unique.length} recorded checks</summary><RecordedChecks checks={unique}/></details>;
}

function Intro({ recipe, setRecipe, mode, setMode, create, busy, health, liveEnabled }: { recipe: Recipe; setRecipe: (recipe: Recipe) => void; mode: Mode; setMode: (mode: Mode) => void; create: () => void; busy: boolean; health: string; liveEnabled: boolean }) {
  return <main className="intro">
    <section className="intro-copy"><p className="eyebrow">PAIRED VISUAL DATA</p><h1>Two captures.<br/><span>One change.</span></h1><p className="intro-lead">{recipe === 'spatial' ? 'Take two photos: bottle left of the cup, then bottle right of the cup. Keep the cup and camera still between photos.' : 'Record two short sequences with opposite arrival orders. Keep the same camera view and starting positions.'}</p><div className="intro-footnote"><span className="rule-line"/>See the setup. Take both captures. Save the pair.</div></section>
    <section className="start-panel" aria-label="Start a collection"><div className="panel-heading"><span className="eyebrow">01 / CHOOSE THE CHANGE</span><span className="tiny-label">CUP + BOTTLE</span></div>
      <button className={`recipe-option ${recipe === 'spatial' ? 'selected' : ''}`} onClick={() => setRecipe('spatial')} aria-pressed={recipe === 'spatial'}><span className="recipe-icon">⇄</span><span><strong>Switch sides</strong><small>A bottle changes sides. The cup stays put.</small></span><span className="radio"/></button>
      <button className={`recipe-option ${recipe === 'temporal' ? 'selected' : ''}`} onClick={() => setRecipe('temporal')} aria-pressed={recipe === 'temporal'}><span className="recipe-icon">①②</span><span><strong>Reverse arrival order</strong><small>The same ending. A different sequence.</small></span><span className="radio"/></button>
      <div className="mode-section"><label className="eyebrow">02 / CHOOSE YOUR SOURCE</label><div className="segmented"><button className={mode === 'replay' ? 'active' : ''} onClick={() => setMode('replay')} aria-pressed={mode === 'replay'}>Explore replay</button><button className={mode === 'live' ? 'active' : ''} onClick={() => setMode('live')} aria-pressed={mode === 'live'}>Use my camera</button></div><p className="source-explanation">{mode === 'replay' ? 'Deterministic test scenes. Explore collection and recovery without using model credits.' : liveEnabled ? 'Use your camera to collect and check a real pair with Roboflow.' : 'Live verification is not enabled on this server yet. Replay is available without model credits.'}</p></div>
      <button className="primary start-button" onClick={create} disabled={busy || (mode === 'live' && !liveEnabled)}>{busy ? 'Opening…' : mode === 'replay' ? 'Open replay session' : 'Start a real capture'}<Arrow/></button>
      <div className="provider-note"><span className={`status-dot ${health === 'unavailable' ? 'offline' : ''}`}/>{health === 'unavailable' ? 'Waiting for the capture service' : 'Built with Roboflow'}<span>Evidence stays paired</span></div>
    </section>
    <section className="intro-diagram"><SetupDiagram temporal={recipe === 'temporal'}/><div className="diagram-caption"><span>THE TASK</span><p>{recipe === 'spatial' ? 'A meaningful change, with the reference held steady.' : 'Two sequences. One ending. Opposite arrival order.'}</p><span className="diagram-index">{recipe === 'spatial' ? 'A ⇄ B' : 'AB / BA'}</span></div></section>
  </main>;
}

export default function App() {
  const [connection, setConnection] = useState<Connection | null>(() => loadConnection());
  const [session, setSession] = useState<Session | null>(null);
  const [recipe, setRecipe] = useState<Recipe>('spatial');
  const [mode, setMode] = useState<Mode>('replay');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [network, setNetwork] = useState<'online' | 'offline' | 'connecting'>('connecting');
  const [health, setHealth] = useState('checking');
  const [liveEnabled, setLiveEnabled] = useState(false);
  const [feedbackClock, setFeedbackClock] = useState(0);
  const previewTimes = useRef(new Map<string, number>());
  const outstandingPreview = useRef<{ id: string; epoch: number; expiresAt: number } | null>(null);
  const wakeSnapshotPoll = useRef<((delay?: number) => void) | null>(null);
  const previewPollingEligible = useRef(false);
  const [pending, setPending] = useState<PendingCapture | null>(null);
  const [pendingLoadedFor, setPendingLoadedFor] = useState<string | null>(null);
  const [pendingConflict, setPendingConflict] = useState<string | null>(null);
  const [resetIntent, setResetIntent] = useState<{ sessionId: string; slot: 'baseline' | 'counterpart'; expected_revision: number; epoch: number; idempotency_key: string } | null>(null);
  const reopenCamera = useRef(false);
  const [guidance, setGuidance] = useState(false);
  const [previewCount, setPreviewCount] = useState(0);
  const [showEvidence, setShowEvidence] = useState(false);
  const [resumeTick, setResumeTick] = useState(0);
  const sessionRef = useRef(session); sessionRef.current = session;
  const needsResume = useRef(!!connection);
  const busyRef = useRef(false); busyRef.current = busy;
  const seq = useRef(0);
  const previewBusy = useRef(false);
  const commandGeneration = useRef(0);
  const markDiscontinuity = useCallback(() => { needsResume.current = true; setGuidance(false); setResumeTick(value => value + 1); }, []);
  const pendingLoaded = !!connection && pendingLoadedFor === connection.id;
  // Even rejected/failed baseline attempts can later be retried or authorized.
  // Their pixel orientation must remain fixed for the rest of this collection.
  const hasSavedCameraEvidence = !!session?.baseline || !!pending || (!!session?.latest_capture && session.latest_capture.slot !== 'preview');
  const camera = useCamera(markDiscontinuity, { sessionId: session?.id || connection?.id, hasSavedEvidence: hasSavedCameraEvidence, orientationLocked: hasSavedCameraEvidence || !pendingLoaded || busy || previewBusy.current });
  useEffect(() => {
    if (!reopenCamera.current || !session) return;
    reopenCamera.current = false;
    if (session.mode === 'live' && session.state !== 'ready' && session.state !== 'published') void camera.start();
  }, [session?.revision, session?.epoch, camera.start]);
  previewPollingEligible.current = guidance && camera.status === 'ready' && !camera.recording && session?.mode === 'live' && session.state !== 'published';

  useEffect(() => { request<Record<string, unknown>>('/health').then(data => { setHealth(String(data.provider_mode || data.provider || 'ready')); setLiveEnabled(data.live_enabled === true); }).catch(() => setHealth('unavailable')); }, []);

  useEffect(() => {
    if (!connection) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    let failures = 0;
    let requestedWake: number | null = null;
    const abort = new AbortController();
    const nextDelay = (snapshot: Session | null) => {
      if (failures) return Math.min(1500 * 2 ** Math.min(failures - 1, 3), 10000);
      const preview = outstandingPreview.current;
      if (preview && (preview.epoch !== snapshot?.epoch || preview.expiresAt <= performance.now())) outstandingPreview.current = null;
      if (previewPollingEligible.current && outstandingPreview.current) return 250;
      return pendingStatus(snapshot?.latest_capture) ? 650 : 1500;
    };
    const schedule = (delay: number) => {
      clearTimeout(timer); timer = undefined;
      if (!cancelled && !document.hidden) timer = setTimeout(() => void poll(), delay);
    };
    const wake = (delay?: number) => {
      if (cancelled) return;
      const wait = delay ?? nextDelay(sessionRef.current);
      if (inFlight) { requestedWake = requestedWake === null ? wait : Math.min(requestedWake, wait); return; }
      schedule(wait);
    };
    const poll = async () => {
      if (cancelled || document.hidden || inFlight) return;
      inFlight = true; timer = undefined;
      const generation = commandGeneration.current;
      let latestSnapshot = sessionRef.current;
      try {
        let snapshot = await request<Session>(`/sessions/${connection.id}`, connection, { signal: abort.signal });
        if (needsResume.current && !busyRef.current) {
          snapshot = await request<Session>(`/sessions/${connection.id}/resume`, connection, { method: 'POST', signal: abort.signal, body: JSON.stringify({ expected_epoch: snapshot.epoch }) });
          needsResume.current = false;
        }
        if (!cancelled) {
          failures = 0;
          if (generation === commandGeneration.current) {
            latestSnapshot = snapshot;
            setSession(snapshot);
            const feedback = snapshot.feedback && 'id' in snapshot.feedback ? snapshot.feedback : null;
            const preview = outstandingPreview.current;
            if (preview && feedback?.id === preview.id && feedback.epoch === preview.epoch && !pendingStatus(feedback)) outstandingPreview.current = null;
          }
          setNetwork('online');
        }
      } catch (cause) {
        if (!cancelled) {
          failures++;
          if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) { setError('This saved session is no longer available. Start a new collection.'); saveConnection(null); setConnection(null); setSession(null); }
          else { setNetwork('offline'); needsResume.current = true; }
        }
      } finally {
        inFlight = false;
        if (!cancelled) {
          const wait = nextDelay(latestSnapshot);
          schedule(requestedWake === null || failures ? wait : Math.min(wait, requestedWake));
          requestedWake = null;
        }
      }
    };
    const visibilityChanged = () => {
      if (document.hidden) { clearTimeout(timer); timer = undefined; }
      else wake(0);
    };
    wakeSnapshotPoll.current = wake;
    document.addEventListener('visibilitychange', visibilityChanged);
    readPending(connection.id).then(value => { if (!cancelled) setPending(value || null); }).catch(() => { if (!cancelled) setNotice('Browser storage is unavailable. Keep this page open until each capture is saved.'); }).finally(() => { if (!cancelled) setPendingLoadedFor(connection.id); });
    void poll();
    return () => {
      cancelled = true; abort.abort(); clearTimeout(timer);
      document.removeEventListener('visibilitychange', visibilityChanged);
      if (wakeSnapshotPoll.current === wake) wakeSnapshotPoll.current = null;
    };
  }, [connection]);

  useEffect(() => { if (resumeTick > 0) wakeSnapshotPoll.current?.(0); }, [resumeTick]);
  useEffect(() => {
    if (!guidance || camera.status !== 'ready' || camera.recording) {
      outstandingPreview.current = null;
      wakeSnapshotPoll.current?.();
    }
  }, [guidance, camera.status, camera.recording]);

  useEffect(() => {
    const offline = () => { setNetwork('offline'); markDiscontinuity(); };
    const online = () => { setNetwork('connecting'); markDiscontinuity(); };
    window.addEventListener('offline', offline); window.addEventListener('online', online);
    return () => { window.removeEventListener('offline', offline); window.removeEventListener('online', online); };
  }, [markDiscontinuity]);

  useEffect(() => {
    if (!guidance || !connection || camera.status !== 'ready' || camera.recording || previewCount >= 12) return;
    const timer = setInterval(async () => {
      const current = sessionRef.current;
      if (!current || busyRef.current || previewBusy.current || pendingStatus(current.latest_capture) || current.state === 'published' || needsResume.current) return;
      previewBusy.current = true;
      try {
        const capturedAt = performance.now();
        const blob = await camera.takePhoto();
        const packet: PendingCapture = { sessionId: connection.id, createdAt: Date.now(), blobs: [blob], metadata: { slot: 'preview', expected_revision: current.revision, epoch: current.epoch, client_seq: ++seq.current, idempotency_key: crypto.randomUUID(), timestamps_ms: [0], interrupted: false } };
        const response = await submitPending(connection, packet);
        if (sessionRef.current?.id !== connection.id || sessionRef.current.epoch !== current.epoch) return;
        previewTimes.current.set(response.capture.id, capturedAt);
        if (previewPollingEligible.current) {
          outstandingPreview.current = { id: response.capture.id, epoch: current.epoch, expiresAt: performance.now() + 4000 };
          wakeSnapshotPoll.current?.(250);
        }
        if (previewTimes.current.size > 16) previewTimes.current.delete(previewTimes.current.keys().next().value!);
        setPreviewCount(value => value + 1);
      } catch (cause) {
        setGuidance(false); setNotice(`Live guidance paused. ${errorMessage(cause)}`);
      } finally { previewBusy.current = false; }
    }, 2000);
    return () => clearInterval(timer);
  }, [guidance, connection, camera.status, camera.recording, camera.takePhoto, previewCount]);

  useEffect(() => {
    if (!guidance && !session?.feedback) return;
    const timer = setInterval(() => setFeedbackClock(value => value + 1), 500);
    return () => clearInterval(timer);
  }, [guidance, session?.feedback]);

  useEffect(() => {
    if (session && connection) void rememberSession(connection, session).catch(() => setNotice('Browser storage could not update your session library. Keep this page open until your evidence is downloaded.'));
  }, [connection, session?.state]);

  useEffect(() => {
    if ((session?.state === 'ready' && !session.human_review_required) || session?.state === 'published') { camera.stop(); setGuidance(false); }
  }, [session?.state, session?.human_review_required, camera.stop]);

  const act = async (task: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(null); commandGeneration.current += 1;
    try { await task(); } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); busyRef.current = false; commandGeneration.current += 1; }
  };
  const create = () => act(async () => {
    await ensureHistoryCapacity();
    const result = await request<{ token: string; session: Session }>('/sessions', null, { method: 'POST', body: JSON.stringify({ recipe, mode, object_a: 'bottle', object_b: 'cup' }) });
    const next = { id: result.session.id, token: result.token };
    saveConnection(next); await rememberSession(next, result.session).catch(() => undefined); needsResume.current = false; setConnection(next); setSession(result.session); setNetwork('online'); setPending(null); setNotice(null);
  });
  const replay = (scenario: Scenario) => act(async () => {
    if (!connection || !session) return;
    const result = await request<{ capture: Capture; session: Session }>(`/sessions/${connection.id}/replay`, connection, { method: 'POST', body: JSON.stringify({ scenario, expected_revision: session.revision, epoch: session.epoch, idempotency_key: crypto.randomUUID() }) });
    setSession(result.session); setNotice(null);
  });
  const sendPacket = async (packet: PendingCapture) => {
    if (!connection) return;
    setPending(packet);
    try { await savePending(packet); }
    catch (cause) { setNotice(`This capture is held in memory only. ${errorMessage(cause)}`); }
    let response;
    try { response = await submitPending(connection, packet); }
    catch (cause) {
      if (cause instanceof ApiError && cause.status === 409 && ['stale_epoch', 'stale_revision', 'recovery_required', 'already_published'].includes(cause.code)) {
        const message = 'This unconfirmed capture belongs to an older scene or session revision. Its images are still saved on this device. Discard the local copy, recheck the scene if requested, then take a new capture. Your accepted server captures are unchanged.';
        setPendingConflict(message);
        throw new Error(message);
      }
      throw cause;
    }
    setPendingConflict(null); setSession(response.session); setPending(null);
    await clearPending(connection.id).catch(() => undefined);
  };
  const capture = () => act(async () => {
    if (!connection || !session) return;
    const slot: Slot = session.recovery_required && !session.human_review_required ? 'recovery' : session.baseline ? 'counterpart' : 'baseline';
    setGuidance(false);
    if (!await camera.startCountdown()) return;
    const current = sessionRef.current;
    if (needsResume.current || current?.id !== session.id || current.epoch !== session.epoch || current.revision !== session.revision) {
      throw new Error('The session changed during the countdown. Check the scene and capture again.');
    }
    let blobs: Blob[]; let timestamps: number[];
    if (session.recipe === 'temporal' && slot !== 'recovery') {
      const recording = await camera.recordSequence();
      if (recording.interrupted) throw new Error(recording.reason || 'The recording was interrupted. Please record again.');
      if (recording.blobs.length < 8) throw new Error('Record at least two seconds, including a still start and finish.');
      blobs = recording.blobs; timestamps = recording.timestamps;
    } else { blobs = [await camera.takePhoto()]; timestamps = [0]; }
    const packet: PendingCapture = { sessionId: connection.id, createdAt: Date.now(), blobs, metadata: { slot, expected_revision: session.revision, epoch: session.epoch, client_seq: ++seq.current, idempotency_key: crypto.randomUUID(), timestamps_ms: timestamps, interrupted: false } };
    await sendPacket(packet);
  });
  const retryVerification = () => act(async () => {
    if (!connection || !session?.latest_capture) return;
    const response = await request<{ session: Session }>(`/sessions/${connection.id}/retry`, connection, { method: 'POST', body: JSON.stringify({ capture_id: session.latest_capture.id, expected_revision: session.revision, epoch: session.epoch, idempotency_key: crypto.randomUUID() }) });
    setSession(response.session);
  });
  const retry = () => act(async () => { if (pending) await sendPacket(pending); });
  const discard = () => act(async () => { if (connection) await clearPending(connection.id); setPending(null); setPendingConflict(null); setNotice('Unconfirmed local capture discarded. Saved server captures are unchanged.'); });
  const resetCapture = (slot: 'baseline' | 'counterpart') => act(async () => {
    if (!connection || !session) return;
    setGuidance(false);
    const intent = resetIntent?.sessionId === session.id ? resetIntent : { sessionId: session.id, slot, expected_revision: session.revision, epoch: session.epoch, idempotency_key: crypto.randomUUID() };
    setResetIntent(intent);
    let snapshot: Session;
    try {
      snapshot = await request<Session>(`/sessions/${connection.id}/reset`, connection, { method: 'POST', body: JSON.stringify({ slot: intent.slot, expected_revision: intent.expected_revision, epoch: intent.epoch, idempotency_key: intent.idempotency_key }) });
    } catch (cause) {
      if (cause instanceof ApiError && cause.status < 500 && cause.status !== 429) setResetIntent(null);
      throw cause;
    }
    let storageWarning = '';
    if (pending) {
      try { await clearPending(connection.id); }
      catch { storageWarning = ' Browser storage could not remove the old local upload. It cannot be used for this reset pair; discard it if it reappears after a reload.'; }
    }
    reopenCamera.current = true;
    setSession(snapshot); setPending(null); setPendingConflict(null); setResetIntent(null);
    needsResume.current = false; outstandingPreview.current = null; previewTimes.current.clear();
    setNotice((intent.slot === 'counterpart' && !snapshot.counterpart
      ? 'First capture kept. Take the second again.'
      : snapshot.latest_capture || snapshot.baseline
      ? 'This reset was already applied. Showing the latest captures in this collection.'
      : session.recipe === 'spatial'
        ? `Pair reset. Take a new first photo with the ${session.object_a} on the left of the ${session.object_b}, then a second with it on the right.`
        : 'Pair reset. Record the first sequence again, then record the opposite order.') + storageWarning);
  });
  const publish = () => act(async () => {
    if (!session || !connection) return;
    const result = await request<{ session: Session }>(`/sessions/${connection.id}/publish`, connection, { method: 'POST', body: JSON.stringify({ expected_revision: session.revision }) }); setSession(result.session);
  });
  const leave = () => { reopenCamera.current = false; camera.stop(); saveConnection(null); setConnection(null); setSession(null); setPending(null); setPendingConflict(null); setResetIntent(null); setError(null); setNotice(null); setGuidance(false); setPreviewCount(0); };
  const reconnect = () => { needsResume.current = true; setResumeTick(value => value + 1); setNotice('Reloading the saved session. A fresh scene check is required before continuing.'); };

  void feedbackClock;
  const feedbackCapture = session?.feedback && 'id' in session.feedback ? session.feedback : null;
  const feedbackStarted = feedbackCapture ? previewTimes.current.get(feedbackCapture.id) : undefined;
  const feedbackAge = feedbackStarted === undefined ? Infinity : performance.now() - feedbackStarted;
  const liveHint = feedbackCapture?.epoch === session?.epoch && feedbackAge < 10000 ? feedbackCapture?.result : null;
  const latest = session?.latest_capture;
  const running = pendingStatus(latest);
  const result = latest?.result;
  const published = session?.state === 'published';
  const humanRequired = Boolean(session?.human_review_required);
  const complete = (session?.state === 'ready' && !humanRequired) || published;
  const publishedManifest = published && session?.pair?.manifest ? session.pair.manifest as { baseline: Capture; counterpart: Capture; evidence_adjudication?: Record<string, unknown> } : null;
  const displayBaseline = publishedManifest?.baseline || session?.baseline;
  const displayCounterpart = publishedManifest?.counterpart || session?.counterpart;
  const second = !!session?.baseline;
  const recovering = !!session?.recovery_required && !humanRequired;
  const replayMode = session?.mode === 'replay';
  const latestMedia = latest && latest.frames?.length ? latest : session?.baseline;
  const configurationProblem = latest?.status === 'failed' && latest.error ? configurationErrors[latest.error] : undefined;
  const actionDisabled = busy || running || network !== 'online' || !pendingLoaded || !!pending || !!resetIntent || (Boolean(configurationProblem) && !complete);
  const humanAdjudicated = Boolean(publishedManifest?.evidence_adjudication);
  const objectA = session?.object_a || 'bottle';
  const objectB = session?.object_b || 'cup';
  const repairGuidance = registrationGuidance(result, latest, objectA, objectB);
  const previewGuidance = registrationGuidance(liveHint, feedbackCapture, objectA, objectB);
  const contractInstructions = session?.recipe_snapshot?.instructions as Record<string, unknown> | undefined;
  const contractInstruction = contractInstructions?.[recovering ? 'recovery' : second ? 'counterpart' : 'baseline'];
  const instruction = !session ? '' : complete ? published ? 'Your pair is saved.' : 'Both captures are ready.' : recovering ? 'Match your first capture to continue.' : session.recipe === 'spatial' ? second ? `Move only the ${objectA} to the right of the ${objectB}.` : `Place the ${objectA} to the left of the ${objectB}.` : typeof contractInstruction === 'string' ? contractInstruction : second ? `${humanize(objectB)} first. Pause. Then the ${objectA}.` : `${humanize(objectA)} first. Pause. Then the ${objectB}.`;
  const supporting = !session ? '' : complete ? published ? 'Download both captures and their recorded checks.' : 'Check the two captures above, then save them together.' : recovering ? `Your first capture is already saved. Restore its camera view and ${objectB} position, or retake it to start from a new setup.` : session.recipe === 'spatial' ? second ? `Leave the ${objectB} and camera exactly where they were. Move the ${objectA} without turning it.` : `Leave a clear gap. Keep the camera and ${objectB} in place for both photos.` : 'Start both objects on the left, one above the other. Move each into the target area, with a pause between arrivals.';
  const protectedObjects = Array.isArray(session?.recipe_snapshot?.protected_objects) ? session.recipe_snapshot.protected_objects.filter((item): item is string => typeof item === 'string') : [];
  const captureLabel = published ? 'Download complete pair' : humanRequired ? 'Capture for human review' : complete ? 'Save the complete pair' : recovering ? 'Check current scene' : session?.recipe === 'temporal' ? second ? 'Record reverse sequence' : 'Record first sequence' : second ? 'Capture the second image' : 'Capture the first image';
  const primaryLabel = !replayMode && !complete && camera.status !== 'ready' ? camera.status === 'requesting' ? 'Allow camera access…' : 'Open camera' : captureLabel;
  const onPrimary = () => {
    if (published && connection) void act(() => download(connection, 'archive'));
    else if (complete) void publish();
    else if (replayMode) void replay(recovering ? 'recovery' : second ? 'counterpart' : 'baseline');
    else if (camera.status !== 'ready') void camera.start();
    else void capture();
  };

  useEffect(() => {
    const onSpace = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const target = event.target;
      // Preserve typing and native keyboard behavior on focused controls.
      if (target instanceof Element && (target.closest('input, textarea, select, button, a, summary, [role="button"], [role="textbox"]') || (target instanceof HTMLElement && target.isContentEditable))) return;
      if (replayMode || complete || camera.status !== 'ready') return;
      event.preventDefault();
      if (event.repeat || camera.countdown !== null) return;
      if (camera.recording) camera.stopRecording();
      else if (!actionDisabled) onPrimary();
    };
    window.addEventListener('keydown', onSpace);
    return () => window.removeEventListener('keydown', onSpace);
  }, [replayMode, complete, actionDisabled, camera.status, camera.countdown, camera.recording, camera.stopRecording, onPrimary]);

  return <div className="app-shell">
    <header className="topbar"><button className="brand-button" onClick={session ? () => undefined : leave} aria-label="Counterpart home"><Logo/></button><span className="topbar-caption">Collect a pair.<br/>Change one thing.</span><div className="topbar-right"><a className="text-button" href="#/library">Your sessions</a><a className="text-button" href="#/workspace">Workspace</a>{session ? <><span className={`connection-status ${network}`}><span className="status-dot"/>{network === 'online' ? 'Session connected' : network === 'offline' ? 'Connection lost' : 'Connecting'}</span><button className="text-button" onClick={leave} disabled={busy || camera.recording}>New collection <span aria-hidden="true">↗</span></button></> : <span className="topbar-label">VISUAL DATA, COLLECTED WITH INTENT</span>}</div></header>
    {error && <div className="global-message error" role="alert"><span>!</span><p>{error}</p><button onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
    {notice && <div className="global-message notice" role="status"><span>i</span><p>{notice}</p><button onClick={() => setNotice(null)} aria-label="Dismiss notice">×</button></div>}
    {!session && !connection && <Intro recipe={recipe} setRecipe={setRecipe} mode={mode} setMode={setMode} create={create} busy={busy} health={health} liveEnabled={liveEnabled}/>}
    {!session && connection && <main className="loading-session"><span className="loading-dot"/><h1>Opening your saved collection.</h1><p>Your accepted captures stay with the session.</p><button className="text-button" onClick={leave}>Start a new collection</button></main>}
    {session && connection && <main className="collection capture-flow">
      <div className="collection-heading"><div><p className="eyebrow">{titles[session.recipe]}</p><h1>{session.recipe === 'spatial' ? 'Two photos, one change' : 'Two recordings, opposite order'}</h1><p className="collection-subtitle">{session.recipe === 'spatial' ? `${humanize(objectA)} left → ${objectA} right. Keep the ${objectB} and camera still.` : 'Same setup and ending. Change which object arrives first.'}</p></div><div className={`source-badge ${replayMode ? 'replay' : 'live'}`}><span className="status-dot"/>{replayMode ? 'SIMULATION · TEST SCENES' : 'LIVE CAMERA · ROBOFLOW'}</div></div>
      <PairProgress session={session} connection={connection} baseline={displayBaseline} counterpart={displayCounterpart} onRetakeFirst={() => resetCapture('baseline')} onRetakeSecond={() => resetCapture('counterpart')} canRetakeFirst={!!(second || latest || pending || resetIntent)} canRetakeSecond={!!session.counterpart || resetIntent?.slot === 'counterpart'} disabled={busy || camera.recording || network !== 'online' || !pendingLoaded} resetPending={resetIntent?.slot || null}/>
      {pending && <div className="pending-banner" role="status"><div><strong>A capture is waiting for confirmation.</strong><p>{pendingConflict || "Retry uses the same saved images and request identity."}</p></div><button className="secondary" disabled={busy || !!pendingConflict || !!resetIntent} onClick={retry}>Retry saved capture</button><button className="text-button" disabled={busy || !!resetIntent} onClick={discard}>Discard local copy</button></div>}
      <div className={`workspace capture-workspace ${complete ? 'complete' : ''}`}>
        <section className="capture-stage" aria-label="Capture workspace">
          <div className="stage-toolbar"><span><span className={`status-dot ${camera.recording ? 'recording' : ''}`}/>{complete ? 'PAIRED EVIDENCE' : replayMode ? 'RECORDED TEST SCENE' : camera.countdown !== null ? `STARTING IN ${camera.countdown}…` : camera.recording ? `RECORDING · ${camera.elapsed.toFixed(1)} / 12s` : 'LIVE VIEW'}</span><span>{session.recipe === 'temporal' ? 'ARRIVAL ORDER' : 'LEFT / RIGHT AS SHOWN'}</span></div>
          {complete && displayBaseline && displayCounterpart ? <div className="pair-grid"><div><div className="pair-label"><span>A / FIRST</span><strong>{session.recipe === 'spatial' ? `${humanize(objectA)} left` : `${humanize(objectA)} → ${objectB}`}</strong></div><CaptureMedia labels={[objectA, objectB]} capture={displayBaseline} connection={connection}/></div><div><div className="pair-label"><span>B / COUNTERPART</span><strong>{session.recipe === 'spatial' ? `${humanize(objectA)} right` : `${humanize(objectB)} → ${objectA}`}</strong></div><CaptureMedia labels={[objectA, objectB]} capture={displayCounterpart} connection={connection}/></div></div> : <div className="main-media">
            {!replayMode && <div className="camera-shell" style={camera.status === 'ready' && camera.dimensions.width ? { aspectRatio: `${camera.dimensions.width} / ${camera.dimensions.height}` } : undefined}><video ref={camera.videoRef} autoPlay muted playsInline className={camera.status === 'ready' ? '' : 'camera-hidden'} style={{ transform: camera.mirrored ? 'scaleX(-1)' : undefined }} aria-label="Live camera view"/>{camera.status !== 'ready' && <div className="camera-empty"><svg viewBox="0 0 48 40" aria-hidden="true"><path d="M5 10h10l4-6h10l4 6h10v26H5z"/><circle cx="24" cy="22" r="8"/></svg><strong>{camera.status === 'requesting' ? 'Allow camera access in your browser' : 'Your camera view will appear here'}</strong><span>Follow the two setups above.</span></div>}{session.recipe === 'temporal' && camera.status === 'ready' && <div className="zone-overlay" aria-hidden="true"><div className="start-zone"><span>START HERE</span></div><div className="target-zone"><span>TARGET AREA</span></div></div>}{camera.status === 'ready' && session.recipe === 'spatial' && !!session.baseline && <ReferenceGhost session={session} width={camera.dimensions.width} height={camera.dimensions.height}/>}{camera.countdown !== null && <div className="capture-countdown" role="status" aria-label="Capture countdown"><span>{session.recipe === 'temporal' && !recovering ? 'Recording starts in' : 'Photo in'}</span><strong>{camera.countdown}</strong><span>Get in position. Keep the camera still.</span></div>}{camera.recording && <div className="recording-chip"><span/>{camera.frameCount} frames saved</div>}</div>}
            {replayMode && (latestMedia ? <CaptureMedia labels={[objectA, objectB]} capture={latestMedia} connection={connection}/> : <SetupDiagram objectA={objectA} objectB={objectB} temporal={session.recipe === 'temporal'}/>)}
          </div>}
          {!replayMode && !complete && <details className="camera-options"><summary>Camera options</summary><div className="camera-orientation"><label><input type="checkbox" checked={camera.mirrored} onChange={event => camera.setMirrored(event.target.checked)} disabled={camera.status !== 'ready' || hasSavedCameraEvidence || busy || running || camera.recording || previewBusy.current}/>Mirror camera</label><span>{hasSavedCameraEvidence ? 'Retake photo 1 to change orientation. ' : ''}The photo is saved exactly as shown.</span></div><div className="stage-footer"><span>Space to capture · 2-second countdown</span><label className="guidance-toggle"><input type="checkbox" checked={guidance && previewCount < 12} onChange={event => setGuidance(event.target.checked)} disabled={camera.status !== 'ready' || busy || camera.recording || previewCount >= 12}/>{previewCount >= 12 ? 'Preview allowance used' : 'Live guidance'}</label></div></details>}
          {!replayMode && (guidance || liveHint) && <div className="live-hint" role="status"><strong>{liveHint ? `Live hint · ${(feedbackAge / 1000).toFixed(1)}s old` : "Waiting for a current view…"}</strong><span>{previewGuidance?.message || liveHint?.message || "Preview feedback never approves a saved capture."}</span></div>}
          {camera.error && !replayMode && <div className="inline-warning" role="status">{camera.error}</div>}
          {replayMode && !complete && second && !recovering && <div className="replay-tools"><div><span className="eyebrow">TRY A NEAR MISS</span><p>See why a plausible pair should be rejected.</p></div><div className="replay-actions"><button className="secondary" onClick={() => replay('camera_moved')} disabled={actionDisabled}>Move the reference</button><button className="secondary" onClick={() => replay('wrong_relation')} disabled={actionDisabled}>Wrong {session.recipe === 'spatial' ? 'side' : 'order'}</button><button className="text-button" onClick={() => replay('ambiguous')} disabled={actionDisabled}>Ambiguous scene</button></div></div>}
        </section>
        <aside className="instruction-panel"><div className="task-copy"><p className="eyebrow">{published ? 'SAVED' : recovering ? 'RESTORE YOUR SETUP' : complete ? 'SAVE YOUR PAIR' : session.recipe === 'spatial' ? second ? 'PHOTO 2 OF 2' : 'PHOTO 1 OF 2' : second ? 'RECORDING 2 OF 2' : 'RECORDING 1 OF 2'}</p><h2>{instruction}</h2><p className="instruction-description">{supporting}</p></div>

          {running && <div className="verification-message checking" role="status"><span className="loading-dot"/><div><strong>{latest?.status === 'running' ? 'Checking the saved evidence' : 'Capture saved. Waiting for verification.'}</strong><p>{replayMode ? 'Evaluating the recorded test observations.' : 'Roboflow supplies detections; the recipe checks the relationship.'}</p>{session.progress && <div className="frame-progress"><progress value={session.progress.completed_frames} max={session.progress.total_frames}/><span>{session.progress.completed_frames} / {session.progress.total_frames} frames checked</span></div>}</div></div>}
          {!running && result && !humanAdjudicated && <div className={`verification-message ${result.accepted ? 'accepted' : 'rejected'}`} role="status"><span>{result.accepted ? '✓' : '!'}</span><div><strong>{result.accepted ? humanRequired ? 'Checks passed; human review required' : latest?.slot === 'recovery' ? 'Ready to continue' : latest?.slot === 'baseline' ? 'First capture saved' : 'Both captures checked' : result.outcome === 'unknown' ? repairGuidance?.title || 'This observation needs a closer look.' : second ? 'Keep the first. Fix this one.' : 'This scene needs a small repair.'}</strong><p>{result.accepted && !humanRequired ? complete ? 'Save the pair when you are happy with both captures.' : 'Follow the next setup shown above.' : repairGuidance?.message || result.message}</p></div></div>}
          {!running && latest?.status === 'failed' && <div className="verification-message rejected" role="status"><span>!</span><div><strong>{configurationProblem ? 'Operator action needed' : 'Verification could not finish'}</strong><p>{configurationProblem || latest.error || 'Your saved evidence is retained. Try again after the service recovers.'}</p><button className="secondary" onClick={retryVerification} disabled={busy || recovering || network !== 'online'}>{configurationProblem ? 'Retry after the operator fixes the service' : 'Retry verification of saved images'}</button></div></div>}
          <div className="primary-action">{camera.countdown !== null ? <button className="primary" onClick={camera.cancelCountdown}>Cancel countdown <span aria-hidden="true">×</span></button> : camera.recording ? <button className="primary recording-button" onClick={camera.stopRecording}>Stop & check sequence <span>■</span></button> : <button className="primary" onClick={onPrimary} disabled={actionDisabled || camera.status === 'requesting'}>{busy ? 'Saving…' : running ? 'Checking…' : primaryLabel}<Arrow/></button>}<p>{published ? 'ZIP includes the manifest and original evidence.' : humanRequired ? 'Saved for operator review. Capturing does not publish a dataset pair.' : replayMode ? 'Simulation only. Nothing is sent to a Roboflow dataset.' : session.recipe === 'temporal' && !complete ? 'Space to start or stop · 2-second countdown · 12 seconds maximum' : 'Press Space or click capture · 2-second countdown.'}</p></div>
          {protectedObjects.length > 0 && !complete && <p className="protected-capture-note">Also keep {protectedObjects.join(", ")} visible and still. The camera and background are part of the recorded controls.</p>}<Checks result={result}/>
          {humanRequired && !published && <div className="human-provenance"><strong>Collecting for human review</strong><p>The operator authorized this uncertain baseline so you can capture its counterpart. An operator must establish the final pair from both original observations.{session.recovery_required ? ' The scene has not been reverified after reconnection; that uncertainty remains recorded.' : ''}</p></div>}{humanAdjudicated && <div className="human-provenance"><strong>Established by human review</strong><p>The original machine decision remains recorded. This pair was established by a separate human adjudication.</p></div>}{published && session.campaign_id && <div className={`collector-review ${session.review?.status || 'pending'}`} role="status"><strong>{session.review?.status === 'approved' ? 'Approved for this collection' : session.review?.status === 'rejected' ? 'The reviewer rejected this pair' : session.review?.status === 'needs_revision' ? 'The reviewer requested another pair' : 'Saved. Awaiting operator review.'}</strong>{session.review?.reason && <p>{session.review.reason}</p>}{['rejected', 'needs_revision'].includes(session.review?.status || '') && <p>The original evidence is preserved. Use your invitation to collect a new pair, or contact the collection owner.</p>}</div>}
          {published && <button className="text-button manifest-link" onClick={() => act(() => download(connection, 'manifest'))}>Download manifest JSON <Arrow/></button>}
        </aside>
      </div>
      {session.baseline && latest?.status === 'rejected' && latest.id !== session.baseline.id && <section className="repair-evidence" aria-label="Compare the rejected capture"><p className="eyebrow">KEEP WHAT WORKED</p><h2>{humanRequired ? 'Review the recorded difference.' : 'Compare. Repair. Continue.'}</h2><p>{result?.message} The reference is preserved below; the rejected attempt is saved separately.</p><div className="repair-grid"><div><h3>{humanRequired ? 'A / First recorded observation' : 'A / Accepted reference'}</h3><CaptureMedia capture={session.baseline} connection={connection} labels={[objectA, objectB]}/></div><div><h3>{humanRequired ? 'B / Counterpart for review' : 'B / Attempt to repair'}</h3><CaptureMedia capture={latest} connection={connection} labels={[objectA, objectB]}/></div></div></section>}
      <section className="evidence-section"><button className="evidence-heading" onClick={() => setShowEvidence(value => !value)} aria-expanded={showEvidence}><span><span className="eyebrow">UNDER THE SURFACE</span><strong>Evidence & session history</strong></span><span>{showEvidence ? '−' : '+'}</span></button>{showEvidence && <div className="evidence-body"><div className="evidence-summary"><div><span>Session revision</span><strong>{session.revision}</strong></div><div><span>Connection epoch</span><strong>{session.epoch}</strong></div><div><span>Evidence source</span><strong>{replayMode ? 'Test fixture' : 'Roboflow'}</strong></div><div><span>Publication</span><strong>{published ? 'Complete local record' : 'Not published'}</strong></div></div><p className="evidence-explanation">A late response cannot approve a newer capture. Retries keep the same identity. Publication preserves both original observations and the machine or human decisions establishing the pair. A local record does not imply a completed Roboflow export.</p><ol className="events">{session.events.length ? session.events.slice(-10).reverse().map((event, i) => <li key={String(event.id || i)}><span className="event-dot"/><div><strong>{String(event.message || humanize(String(event.type || event.kind || event.event || 'Session updated')))}</strong>{Boolean(event.created_at || event.at) && <time>{new Date(String(event.created_at || event.at)).toLocaleTimeString()}</time>}</div></li>) : <li>Capture activity will appear here.</li>}</ol>{!complete && <button className="secondary" onClick={reconnect} disabled={actionDisabled}>Reconnect & check recovery</button>}</div>}</section>
    </main>}
    <footer className="site-footer"><span>counterpart / built with roboflow</span><span>Collect deliberately.</span></footer>
  </div>;
}
