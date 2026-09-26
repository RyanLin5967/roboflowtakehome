import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, authorizedBlob, clearPending, download, loadConnection, readPending, request, saveConnection, savePending, submitPending } from './api';
import type { Capture, Connection, Mode, PendingCapture, Recipe, Result, Scenario, Session, Slot } from './api';
import { useCamera } from './camera';

const titles: Record<Recipe, string> = { spatial: 'Switch sides', temporal: 'Reverse arrival order' };
const pendingStatus = (capture: Capture | null | undefined) => !!capture && ['queued', 'running'].includes(capture.status);
const humanize = (text: string) => text.replaceAll('_', ' ').replace(/^./, value => value.toUpperCase());
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';

function Logo() { return <span className="brand"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M4 7h10v18H4zM18 7h10v18H18z" fill="none" stroke="currentColor" strokeWidth="2"/><path d="M11 16h10m-4-4 4 4-4 4" fill="none" stroke="currentColor" strokeWidth="2"/></svg>counterpart<span className="brand-period">.</span></span>; }
function Arrow() { return <span aria-hidden="true">↗</span>; }
function SetupDiagram({ temporal = false }: { temporal?: boolean }) {
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

type CachedAsset = { references: number; url?: string; promise: Promise<string> };
const mediaCache = new Map<string, CachedAsset>();
function retainAsset(connection: Connection, assetId: string) {
  const key = `${connection.id}/${assetId}`;
  let entry = mediaCache.get(key);
  if (!entry) {
    const created: CachedAsset = { references: 0, promise: Promise.resolve('') };
    mediaCache.set(key, created);
    created.promise = authorizedBlob(connection, `assets/${encodeURIComponent(assetId)}`).then(async blob => {
      const url = URL.createObjectURL(blob);
      const image = new Image(); image.src = url;
      try { await image.decode(); } catch { URL.revokeObjectURL(url); throw new Error('Saved image cannot be decoded.'); }
      created.url = url;
      if (!created.references) { URL.revokeObjectURL(url); mediaCache.delete(key); }
      return url;
    });
    entry = created;
  }
  entry.references++;
  const retained = entry;
  return { promise: retained.promise, release: () => {
    retained.references--;
    if (!retained.references && retained.url) { URL.revokeObjectURL(retained.url); mediaCache.delete(key); }
  } };
}

function CaptureMedia({ capture, connection, small = false }: { capture: Capture; connection: Connection; small?: boolean }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState(false);
  const frameKey = capture.frames.map(frame => frame.asset_id).join('|');
  const timingKey = capture.frames.map(frame => frame.t_ms).join('|');
  const displayRef = useRef<{ captureId: string; index: number } | null>(null);
  useEffect(() => { setIndex(0); setPlaying(false); }, [capture.id]);
  useEffect(() => {
    let cancelled = false;
    setFailed(false); setUrls({});
    const retained = frameKey.split('|').filter(Boolean).map(assetId => {
      const asset = retainAsset(connection, assetId);
      asset.promise.then(url => { if (!cancelled) setUrls(current => ({ ...current, [assetId]: url })); }).catch(() => { if (!cancelled) setFailed(true); });
      return asset;
    });
    return () => { cancelled = true; retained.forEach(asset => asset.release()); };
  }, [connection, frameKey]);
  const targetIndex = Math.min(index, capture.frames.length - 1);
  if (capture.frames[targetIndex] && urls[capture.frames[targetIndex].asset_id]) displayRef.current = { captureId: capture.id, index: targetIndex };
  const shownIndex = displayRef.current?.captureId === capture.id ? displayRef.current.index : targetIndex;
  const frame = capture.frames[shownIndex];
  const url = frame ? urls[frame.asset_id] : undefined;
  useEffect(() => {
    if (!playing || !url || shownIndex !== targetIndex) return;
    const nextIndex = (targetIndex + 1) % capture.frames.length;
    const delay = nextIndex === 0 ? 650 : Math.max(40, capture.frames[nextIndex].t_ms - capture.frames[targetIndex].t_ms);
    const timer = setTimeout(() => setIndex(nextIndex), delay);
    return () => clearTimeout(timer);
  }, [playing, frameKey, timingKey, targetIndex, shownIndex, url]);
  const detections = frame?.detections?.filter(detection => ['bottle', 'cup'].includes(detection.label)) || [];
  return <div className={`capture-media ${small ? 'small' : ''}`}>
    <div className="saved-frame" style={frame ? { aspectRatio: `${frame.width} / ${frame.height}` } : undefined}>{url ? <><img src={url} alt={`${capture.slot === 'baseline' ? 'First' : 'Second'} saved capture${capture.frames.length > 1 ? `, frame ${shownIndex + 1}` : ''}`} />{!small && detections.length > 0 && <svg className="detection-overlay" viewBox={`0 0 ${frame.width} ${frame.height}`} aria-label="Recorded object detections">{detections.map((detection, i) => <g key={`${detection.label}-${i}`} className={detection.label === 'bottle' ? 'bottle-detection' : 'cup-detection'}><rect x={detection.box[0] * frame.width} y={detection.box[1] * frame.height} width={(detection.box[2] - detection.box[0]) * frame.width} height={(detection.box[3] - detection.box[1]) * frame.height} fill="none" strokeWidth="2" vectorEffect="non-scaling-stroke"/><text x={detection.box[0] * frame.width + 4} y={Math.max(16, detection.box[1] * frame.height - 7)} fontSize={Math.max(12, frame.width / 55)}>{detection.label} · {Math.round(detection.confidence * 100)}%</text></g>)}</svg>}</> : <div className="media-placeholder">{failed ? 'Saved image unavailable' : 'Loading saved evidence…'}</div>}</div>
    {capture.frames.length > 1 && !small && <div className="sequence-controls"><button className="icon-button" onClick={() => setPlaying(value => !value)} aria-label={playing ? 'Pause sequence' : 'Play sequence'}>{playing ? 'Ⅱ' : '▶'}</button><input aria-label="Sequence frame" type="range" min="0" max={capture.frames.length - 1} value={index} onChange={event => { setPlaying(false); setIndex(Number(event.target.value)); }}/><span>{((frame?.t_ms || 0) / 1000).toFixed(2)}s</span><span className="sequence-label">{capture.frames.length} sampled frames</span></div>}
  </div>;
}

function Checks({ result }: { result?: Result | null }) {
  if (!result?.checks?.length) return null;
  const labels: Record<string, string> = { visible_unambiguous_objects: 'Cup and bottle clearly visible', requested_relation: 'Requested side confirmed', reference_position: 'Cup stayed in place', requested_order: 'Arrival order confirmed', observation_gaps: 'No missing action interval', bottle_matching_endpoints: 'Bottle endpoints match', cup_matching_endpoints: 'Cup endpoints match' };
  const unique = [...new Map(result.checks.map(check => [`${check.name}:${check.passed}`, check])).values()];
  const failures = unique.filter(check => !check.passed);
  const summary = failures.length ? failures.slice(0, 1) : unique.filter(check => labels[check.name]).slice(0, 3);
  return <div className="verification-checks"><ul className="checks concise-checks">{summary.map((check, i) => <li key={`${check.name}-${i}`} className={check.passed ? 'pass' : 'fail'}><span className="check-mark" aria-hidden="true">{check.passed ? '✓' : '!'}</span><span>{check.passed ? labels[check.name] || humanize(check.name) : check.detail || humanize(check.name)}</span></li>)}</ul><details className="check-details"><summary>View all {unique.length} recorded checks</summary><ul className="checks">{unique.map((check, i) => <li key={`${check.name}-${i}`} className={check.passed ? 'pass' : 'fail'}><span className="check-mark" aria-hidden="true">{check.passed ? '✓' : '!'}</span><span>{humanize(check.name)}{check.detail && <small>{check.detail}</small>}</span></li>)}</ul></details></div>;
}

function Intro({ recipe, setRecipe, mode, setMode, create, busy, health, liveEnabled }: { recipe: Recipe; setRecipe: (recipe: Recipe) => void; mode: Mode; setMode: (mode: Mode) => void; create: () => void; busy: boolean; health: string; liveEnabled: boolean }) {
  return <main className="intro">
    <section className="intro-copy"><p className="eyebrow">A COLLECTION INSTRUMENT</p><h1>Collect the<br/><span>difference.</span></h1><p className="intro-lead">Make two observations that tell different stories. Change what matters. Keep the evidence together.</p><div className="intro-footnote"><span className="rule-line"/>Guided capture. Grounded checks. One complete pair.</div></section>
    <section className="start-panel" aria-label="Start a collection"><div className="panel-heading"><span className="eyebrow">01 / CHOOSE THE CHANGE</span><span className="tiny-label">CUP + BOTTLE</span></div>
      <button className={`recipe-option ${recipe === 'spatial' ? 'selected' : ''}`} onClick={() => setRecipe('spatial')} aria-pressed={recipe === 'spatial'}><span className="recipe-icon">⇄</span><span><strong>Switch sides</strong><small>A bottle changes sides. The cup stays put.</small></span><span className="radio"/></button>
      <button className={`recipe-option ${recipe === 'temporal' ? 'selected' : ''}`} onClick={() => setRecipe('temporal')} aria-pressed={recipe === 'temporal'}><span className="recipe-icon">①②</span><span><strong>Reverse arrival order</strong><small>The same ending. A different sequence.</small></span><span className="radio"/></button>
      <div className="mode-section"><label className="eyebrow">02 / CHOOSE YOUR SOURCE</label><div className="segmented"><button className={mode === 'replay' ? 'active' : ''} onClick={() => setMode('replay')} aria-pressed={mode === 'replay'}>Explore replay</button><button className={mode === 'live' ? 'active' : ''} onClick={() => setMode('live')} aria-pressed={mode === 'live'}>Use my camera</button></div><p className="source-explanation">{mode === 'replay' ? 'Deterministic test scenes. Explore collection and recovery without using model credits.' : liveEnabled ? 'Your camera, with server-side Roboflow verification. No microphone. No model runs on your device.' : 'Live verification is not enabled on this server yet. Replay is available without model credits.'}</p></div>
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
  const [pending, setPending] = useState<PendingCapture | null>(null);
  const [pendingConflict, setPendingConflict] = useState<string | null>(null);
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
  const camera = useCamera(markDiscontinuity);

  useEffect(() => { request<Record<string, unknown>>('/health').then(data => { setHealth(String(data.provider_mode || data.provider || 'ready')); setLiveEnabled(data.live_enabled === true); }).catch(() => setHealth('unavailable')); }, []);

  useEffect(() => {
    if (!connection) return;
    let cancelled = false; let timer: ReturnType<typeof setTimeout>;
    readPending(connection.id).then(value => { if (!cancelled) setPending(value || null); }).catch(() => { if (!cancelled) setNotice('Browser storage is unavailable. Keep this page open until each capture is saved.'); });
    const poll = async () => {
      const generation = commandGeneration.current;
      try {
        let snapshot = await request<Session>(`/sessions/${connection.id}`, connection);
        if (needsResume.current && !busyRef.current) {
          snapshot = await request<Session>(`/sessions/${connection.id}/resume`, connection, { method: 'POST', body: JSON.stringify({ expected_epoch: snapshot.epoch }) });
          needsResume.current = false;
        }
        if (!cancelled) {
          if (generation === commandGeneration.current) setSession(snapshot);
          setNetwork('online');
        }
      } catch (cause) {
        if (!cancelled) {
          if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) { setError('This saved session is no longer available. Start a new collection.'); saveConnection(null); setConnection(null); setSession(null); }
          else { setNetwork('offline'); needsResume.current = true; }
        }
      } finally {
        if (!cancelled) timer = setTimeout(poll, pendingStatus(sessionRef.current?.latest_capture) ? 650 : 1500);
      }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [connection, resumeTick]);

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
        previewTimes.current.set(response.capture.id, capturedAt);
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
    if (session?.state === 'ready' || session?.state === 'published') { camera.stop(); setGuidance(false); }
  }, [session?.state, camera.stop]);

  const act = async (task: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(null); commandGeneration.current += 1;
    try { await task(); } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); busyRef.current = false; commandGeneration.current += 1; }
  };
  const create = () => act(async () => {
    const result = await request<{ token: string; session: Session }>('/sessions', null, { method: 'POST', body: JSON.stringify({ recipe, mode, object_a: 'bottle', object_b: 'cup' }) });
    const next = { id: result.session.id, token: result.token };
    saveConnection(next); needsResume.current = false; setConnection(next); setSession(result.session); setNetwork('online'); setPending(null); setNotice(null);
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
    const slot: Slot = session.recovery_required ? 'recovery' : session.baseline ? 'counterpart' : 'baseline';
    setGuidance(false);
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
  const retry = () => act(async () => { if (pending) await sendPacket(pending); });
  const discard = () => act(async () => { if (connection) await clearPending(connection.id); setPending(null); setPendingConflict(null); setNotice('Unconfirmed local capture discarded. Saved server captures are unchanged.'); });
  const publish = () => act(async () => {
    if (!session || !connection) return;
    const result = await request<{ session: Session }>(`/sessions/${connection.id}/publish`, connection, { method: 'POST', body: JSON.stringify({ expected_revision: session.revision }) }); setSession(result.session);
  });
  const leave = () => { camera.stop(); saveConnection(null); setConnection(null); setSession(null); setPending(null); setPendingConflict(null); setError(null); setNotice(null); setGuidance(false); setPreviewCount(0); };
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
  const complete = session?.state === 'ready' || published;
  const second = !!session?.baseline;
  const recovering = !!session?.recovery_required;
  const replayMode = session?.mode === 'replay';
  const latestMedia = latest && latest.frames?.length ? latest : session?.baseline;
  const actionDisabled = busy || running || network !== 'online' || !!pending;
  const instruction = !session ? '' : complete ? 'A complete pair. Every observation attached.' : recovering ? 'Your first capture is safe. Check the scene to continue.' : session.recipe === 'spatial' ? second ? 'Move only the bottle to the right of the cup.' : 'Place the bottle to the left of the cup.' : second ? 'Cup first. Pause. Then the bottle.' : 'Bottle first. Pause. Then the cup.';
  const supporting = !session ? '' : complete ? 'Both captures and their checks stay together in one versioned record.' : recovering ? session.recipe === 'temporal' ? 'Restore both objects to their starting lanes and keep the camera steady.' : 'Keep the cup and camera in the same position as your saved capture.' : session.recipe === 'spatial' ? 'Keep the cup in place and the camera steady. Make the gap between the objects clear.' : 'Start both objects in the left zone, one above the other. Move each into the target band. Hold the start and finish still.';
  const primaryLabel = published ? 'Download complete pair' : complete ? 'Save the complete pair' : recovering ? 'Check current scene' : session?.recipe === 'temporal' ? second ? 'Record reverse sequence' : 'Record first sequence' : second ? 'Capture the counterpart' : 'Capture the first image';
  const onPrimary = () => {
    if (published && connection) void act(() => download(connection, 'archive'));
    else if (complete) void publish();
    else if (replayMode) void replay(recovering ? 'recovery' : second ? 'counterpart' : 'baseline');
    else void capture();
  };

  return <div className="app-shell">
    <header className="topbar"><button className="brand-button" onClick={session ? () => undefined : leave} aria-label="Counterpart home"><Logo/></button><span className="topbar-caption">A little more signal.<br/>A lot less ambiguity.</span><div className="topbar-right">{session ? <><span className={`connection-status ${network}`}><span className="status-dot"/>{network === 'online' ? 'Session connected' : network === 'offline' ? 'Connection lost' : 'Connecting'}</span><button className="text-button" onClick={leave} disabled={busy || camera.recording}>New collection <span>↗</span></button></> : <span className="topbar-label">VISUAL DATA, COLLECTED WITH INTENT</span>}</div></header>
    {error && <div className="global-message error" role="alert"><span>!</span><p>{error}</p><button onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
    {notice && <div className="global-message notice" role="status"><span>i</span><p>{notice}</p><button onClick={() => setNotice(null)} aria-label="Dismiss notice">×</button></div>}
    {!session && !connection && <Intro recipe={recipe} setRecipe={setRecipe} mode={mode} setMode={setMode} create={create} busy={busy} health={health} liveEnabled={liveEnabled}/>}
    {!session && connection && <main className="loading-session"><span className="loading-dot"/><h1>Opening your saved collection.</h1><p>Your accepted captures stay with the session.</p><button className="text-button" onClick={leave}>Start a new collection</button></main>}
    {session && connection && <main className="collection">
      <div className="collection-heading"><div><p className="eyebrow">COLLECTION / {session.id.slice(0, 8).toUpperCase()}</p><h1>{titles[session.recipe]}<span className="heading-period">.</span></h1></div><div className={`source-badge ${replayMode ? 'replay' : 'live'}`}><span className="status-dot"/>{replayMode ? 'SIMULATION · TEST SCENES' : 'LIVE CAMERA · ROBOFLOW'}<small>{replayMode ? 'Deterministic data. No model calls.' : 'Cloud perception. Local camera only.'}</small></div></div>
      <div className="steps" aria-label="Collection progress"><div className={second ? 'done' : 'current'}><span>{second ? '✓' : '01'}</span>First observation</div><span className="step-line"/><div className={complete ? 'done' : second ? 'current' : ''}><span>{complete ? '✓' : '02'}</span>The controlled change</div><span className="step-line"/><div className={published ? 'done' : complete ? 'current' : ''}><span>{published ? '✓' : '03'}</span>Complete pair</div></div>
      {pending && <div className="pending-banner" role="status"><div><strong>A capture is waiting for confirmation.</strong><p>{pendingConflict || "Retry uses the same saved images and request identity."}</p></div><button className="secondary" disabled={busy || !!pendingConflict} onClick={retry}>Retry saved capture</button><button className="text-button" disabled={busy} onClick={discard}>Discard local copy</button></div>}
      <div className="mobile-task"><span className="eyebrow">{recovering ? "CHECK THE SCENE" : complete ? "PAIR COMPLETE" : second ? "NEXT: THE CONTROLLED CHANGE" : "FIRST OBSERVATION"}</span><strong>{instruction}</strong></div>
      <div className={`workspace ${complete ? 'complete' : ''}`}>
        <section className="capture-stage" aria-label="Capture workspace">
          <div className="stage-toolbar"><span><span className={`status-dot ${camera.recording ? 'recording' : ''}`}/>{complete ? 'PAIRED EVIDENCE' : replayMode ? 'RECORDED TEST SCENE' : camera.recording ? `RECORDING · ${camera.elapsed.toFixed(1)} / 12s` : 'LIVE VIEW'}</span><span>{session.recipe === 'temporal' ? 'ARRIVAL ORDER' : 'IMAGE-PLANE LEFT / RIGHT'}</span></div>
          {complete && session.baseline && session.counterpart ? <div className="pair-grid"><div><div className="pair-label"><span>A / FIRST</span><strong>{session.recipe === 'spatial' ? 'Bottle left' : 'Bottle → cup'}</strong></div><CaptureMedia capture={session.baseline} connection={connection}/></div><div><div className="pair-label"><span>B / COUNTERPART</span><strong>{session.recipe === 'spatial' ? 'Bottle right' : 'Cup → bottle'}</strong></div><CaptureMedia capture={session.counterpart} connection={connection}/></div></div> : <div className="main-media">
            {!replayMode && <div className="camera-shell" style={camera.status === 'ready' && camera.dimensions.width ? { aspectRatio: `${camera.dimensions.width} / ${camera.dimensions.height}` } : undefined}><video ref={camera.videoRef} autoPlay muted playsInline className={camera.status === 'ready' ? '' : 'camera-hidden'} aria-label="Live camera view"/>{camera.status !== 'ready' && <div className="camera-placeholder"><SetupDiagram temporal={session.recipe === 'temporal'}/><div className="camera-start"><button className="primary" disabled={camera.status === 'requesting'} onClick={camera.start}>{camera.status === 'requesting' ? 'Allow camera access…' : 'Open camera'}<Arrow/></button><p>Camera permission is requested by your browser.</p></div></div>}{session.recipe === 'temporal' && camera.status === 'ready' && <div className="zone-overlay" aria-hidden="true"><div className="start-zone"><span>START HERE</span></div><div className="target-zone"><span>TARGET AREA</span></div></div>}{camera.recording && <div className="recording-chip"><span/>{camera.frameCount} frames saved</div>}</div>}
            {replayMode && (latestMedia ? <CaptureMedia capture={latestMedia} connection={connection}/> : <SetupDiagram temporal={session.recipe === 'temporal'}/>)}
            {second && session.baseline && <div className="baseline-thumbnail"><div><span>✓</span> FIRST CAPTURE SAVED</div><CaptureMedia capture={session.baseline} connection={connection} small/></div>}
          </div>}
          <div className="stage-footer"><span>{replayMode ? 'Simulation runs through the same durable capture pipeline.' : 'Original frames saved. No model runs on this device.'}</span>{!replayMode && !complete && <label className="guidance-toggle"><input type="checkbox" checked={guidance && previewCount < 12} onChange={event => setGuidance(event.target.checked)} disabled={camera.status !== 'ready' || camera.recording || previewCount >= 12}/>{previewCount >= 12 ? 'Preview allowance used' : 'Live guidance'}</label>}</div>
          {!replayMode && (guidance || liveHint) && <div className="live-hint" role="status"><strong>{liveHint ? `Live hint · ${(feedbackAge / 1000).toFixed(1)}s old` : "Waiting for a current view…"}</strong><span>{liveHint?.message || "Preview feedback never approves a saved capture."}</span></div>}
          {camera.error && !replayMode && <div className="inline-warning" role="status">{camera.error}</div>}
          {replayMode && !complete && second && !recovering && <div className="replay-tools"><div><span className="eyebrow">TRY A NEAR MISS</span><p>See why a plausible pair should be rejected.</p></div><div className="replay-actions"><button className="secondary" onClick={() => replay('camera_moved')} disabled={actionDisabled}>Move the reference</button><button className="secondary" onClick={() => replay('wrong_relation')} disabled={actionDisabled}>Wrong {session.recipe === 'spatial' ? 'side' : 'order'}</button><button className="text-button" onClick={() => replay('ambiguous')} disabled={actionDisabled}>Ambiguous scene</button></div></div>}
        </section>
        <aside className="instruction-panel"><div className="instruction-number">{complete ? '03' : second ? '02' : '01'}<span> / 03</span></div><p className="eyebrow">{published ? 'SAVED AS ONE RECORD' : recovering ? 'RESUME YOUR COLLECTION' : complete ? 'READY TO KEEP' : second ? 'MAKE THE CHANGE' : 'SET THE SCENE'}</p><h2>{instruction}</h2><p className="instruction-description">{supporting}</p>
          {running && <div className="verification-message checking" role="status"><span className="loading-dot"/><div><strong>{latest?.status === 'running' ? 'Checking the saved evidence' : 'Capture saved. Waiting for verification.'}</strong><p>{replayMode ? 'Evaluating the recorded test observations.' : 'Roboflow supplies detections; the recipe checks the relationship.'}</p></div></div>}
          {!running && result && <div className={`verification-message ${result.accepted ? 'accepted' : 'rejected'}`} role="status"><span>{result.accepted ? '✓' : '!'}</span><div><strong>{result.accepted ? latest?.slot === 'recovery' ? 'Scene checked' : 'Observation accepted' : second ? 'Keep the first. Fix this one.' : 'This scene needs a small repair.'}</strong><p>{result.message}</p></div></div>}
          {!running && latest?.status === 'failed' && <div className="verification-message rejected" role="status"><span>!</span><div><strong>Verification could not finish</strong><p>{latest.error || 'Your saved evidence is retained. Try again after the service recovers.'}</p></div></div>}
          <div className="primary-action">{camera.recording ? <button className="primary recording-button" onClick={camera.stopRecording}>Stop & check sequence <span>■</span></button> : <button className="primary" onClick={onPrimary} disabled={actionDisabled || (!replayMode && !complete && camera.status !== 'ready')}>{busy ? 'Saving…' : running ? 'Checking…' : primaryLabel}<Arrow/></button>}<p>{published ? 'ZIP includes the manifest and original evidence.' : replayMode ? 'Simulation only. Nothing is sent to a Roboflow dataset.' : session.recipe === 'temporal' && !complete ? '4 sampled frames / second · 12 seconds maximum' : 'The saved capture is checked separately from live guidance.'}</p></div>
          <Checks result={result}/>
          {published && <button className="text-button manifest-link" onClick={() => act(() => download(connection, 'manifest'))}>Download manifest JSON <Arrow/></button>}
          {second && !complete && <div className="preserved-note"><span>↳</span><p>Your first observation stays saved through retries and reconnects.</p></div>}
        </aside>
      </div>
      <section className="evidence-section"><button className="evidence-heading" onClick={() => setShowEvidence(value => !value)} aria-expanded={showEvidence}><span><span className="eyebrow">UNDER THE SURFACE</span><strong>Evidence & session history</strong></span><span>{showEvidence ? '−' : '+'}</span></button>{showEvidence && <div className="evidence-body"><div className="evidence-summary"><div><span>Session revision</span><strong>{session.revision}</strong></div><div><span>Connection epoch</span><strong>{session.epoch}</strong></div><div><span>Evidence source</span><strong>{replayMode ? 'Test fixture' : 'Roboflow'}</strong></div><div><span>Publication</span><strong>{published ? 'Complete local record' : 'Not published'}</strong></div></div><p className="evidence-explanation">A late response cannot approve a newer capture. Retries keep the same identity. Publication requires both accepted observations; a local record does not imply a completed Roboflow export.</p><ol className="events">{session.events.length ? session.events.slice(-10).reverse().map((event, i) => <li key={String(event.id || i)}><span className="event-dot"/><div><strong>{String(event.message || humanize(String(event.type || event.kind || event.event || 'Session updated')))}</strong>{Boolean(event.created_at || event.at) && <time>{new Date(String(event.created_at || event.at)).toLocaleTimeString()}</time>}</div></li>) : <li>Capture activity will appear here.</li>}</ol>{!complete && <button className="secondary" onClick={reconnect} disabled={actionDisabled}>Reconnect & check recovery</button>}</div>}</section>
    </main>}
    <footer className="site-footer"><span>counterpart / built with roboflow</span><span>Collect deliberately.</span></footer>
  </div>;
}
