import { useCallback, useEffect, useRef, useState } from 'react';
import { authorizedBlob } from './api';
import type { Capture, Connection } from './api';

type CachedAsset = { references: number; url?: string; promise: Promise<string> };
const mediaCache = new Map<string, CachedAsset>();
function retainAsset(scope: string, loadAsset: (assetId: string) => Promise<Blob>, assetId: string) {
  const key = `${scope}/${assetId}`;
  let entry = mediaCache.get(key);
  if (!entry) {
    const created: CachedAsset = { references: 0, promise: Promise.resolve('') };
    mediaCache.set(key, created);
    created.promise = loadAsset(assetId).then(async blob => {
      const url = URL.createObjectURL(blob);
      const image = new Image(); image.src = url;
      try { await image.decode(); } catch { URL.revokeObjectURL(url); throw new Error('Saved image cannot be decoded.'); }
      created.url = url;
      if (!created.references) { URL.revokeObjectURL(url); mediaCache.delete(key); }
      return url;
    }).catch(error => { if (mediaCache.get(key) === created) mediaCache.delete(key); throw error; });
    entry = created;
  }
  entry.references++;
  const retained = entry;
  return { promise: retained.promise, release: () => {
    retained.references--;
    if (!retained.references && retained.url) { URL.revokeObjectURL(retained.url); mediaCache.delete(key); }
  } };
}

export function EvidenceMedia({ capture, scope, loadAsset, small = false, labels = ['bottle', 'cup'], showConfidence = true }: { capture: Capture; scope: string; loadAsset: (assetId: string) => Promise<Blob>; small?: boolean; labels?: string[]; showConfidence?: boolean }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState(false);
  const [retryTick, setRetryTick] = useState(0);
  const frameKey = capture.frames.map(frame => frame.asset_id).join('|');
  const timingKey = capture.frames.map(frame => frame.t_ms).join('|');
  const displayRef = useRef<{ captureId: string; index: number } | null>(null);
  useEffect(() => { setIndex(0); setPlaying(false); }, [capture.id]);
  useEffect(() => {
    let cancelled = false;
    setFailed(false); setUrls({});
    const retained = frameKey.split('|').filter(Boolean).map(assetId => {
      const asset = retainAsset(scope, loadAsset, assetId);
      asset.promise.then(url => { if (!cancelled) setUrls(current => ({ ...current, [assetId]: url })); }).catch(() => { if (!cancelled) setFailed(true); });
      return asset;
    });
    return () => { cancelled = true; retained.forEach(asset => asset.release()); };
  }, [scope, loadAsset, frameKey, retryTick]);
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
  const detections = frame?.detections?.filter(detection => labels.includes(detection.label)) || [];
  return <div className={`capture-media ${small ? 'small' : ''}`}>
    <div className="saved-frame" style={frame ? { aspectRatio: `${frame.width} / ${frame.height}` } : undefined}>{url ? <><img src={url} alt={`${capture.slot === 'baseline' ? 'First' : 'Second'} saved capture${capture.frames.length > 1 ? `, frame ${shownIndex + 1}` : ''}`} />{!small && detections.length > 0 && <svg className="detection-overlay" viewBox={`0 0 ${frame.width} ${frame.height}`} aria-label="Recorded object detections">{detections.map((detection, i) => <g key={`${detection.label}-${i}`} className={detection.label === labels[0] ? 'bottle-detection' : 'cup-detection'}><rect x={detection.box[0] * frame.width} y={detection.box[1] * frame.height} width={(detection.box[2] - detection.box[0]) * frame.width} height={(detection.box[3] - detection.box[1]) * frame.height} fill="none" strokeWidth="2" vectorEffect="non-scaling-stroke"/><text x={detection.box[0] * frame.width + 4} y={Math.max(16, detection.box[1] * frame.height - 7)} fontSize={Math.max(12, frame.width / 55)}>{detection.label}{showConfidence ? ` · ${Math.round(detection.confidence * 100)}%` : ''}</text></g>)}</svg>}</> : <div className="media-placeholder">{failed ? <><span>Saved image unavailable</span><button className="secondary" onClick={() => setRetryTick(value => value + 1)}>Retry saved evidence</button></> : 'Loading saved evidence…'}</div>}</div>
    {capture.frames.length > 1 && !small && <div className="sequence-controls"><button className="icon-button" onClick={() => setPlaying(value => !value)} aria-label={playing ? 'Pause sequence' : 'Play sequence'}>{playing ? 'Ⅱ' : '▶'}</button><input aria-label="Sequence frame" type="range" min="0" max={capture.frames.length - 1} value={index} onChange={event => { setPlaying(false); setIndex(Number(event.target.value)); }}/><span>{((frame?.t_ms || 0) / 1000).toFixed(2)}s</span><span className="sequence-label">{capture.frames.length} sampled frames</span></div>}
  </div>;
}

export function CaptureMedia({ capture, connection, small, labels }: { capture: Capture; connection: Connection; small?: boolean; labels?: string[] }) {
  const loadAsset = useCallback((assetId: string) => authorizedBlob(connection, `assets/${encodeURIComponent(assetId)}`), [connection]);
  return <EvidenceMedia capture={capture} scope={`session:${connection.id}`} loadAsset={loadAsset} small={small} labels={labels}/>;
}
