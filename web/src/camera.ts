import { useCallback, useEffect, useRef, useState } from 'react';

export type Recording = { blobs: Blob[]; timestamps: number[]; interrupted: boolean; reason?: string };
export type CameraStatus = 'off' | 'requesting' | 'ready' | 'error';
type CameraOptions = { sessionId?: string; hasSavedEvidence?: boolean; orientationLocked?: boolean };
type FrameWaiter = { resolve: (value: Recording) => void; started: number; frames: Blob[]; timestamps: number[]; lastFrame: number; encoding: boolean; stopping: boolean; interrupted: boolean; reason?: string; timer: ReturnType<typeof setTimeout> };

const orientationKey = (sessionId: string) => `counterpart.camera-orientation.v1:${sessionId}`;
function readOrientation(sessionId?: string): boolean | null {
  if (!sessionId) return null;
  try {
    const value = localStorage.getItem(orientationKey(sessionId));
    return value === 'mirrored' ? true : value === 'original' ? false : null;
  } catch { return null; }
}
function saveOrientation(sessionId: string | undefined, mirrored: boolean): boolean {
  if (!sessionId) return false;
  try {
    const value = mirrored ? 'mirrored' : 'original';
    localStorage.setItem(orientationKey(sessionId), value);
    return localStorage.getItem(orientationKey(sessionId)) === value;
  } catch { return false; }
}

/** Rechecked sessions retain the pixel coordinate system of their original evidence. */
export function inheritCameraOrientation(sourceId: string, targetId: string): void {
  const unavailable = 'Your saved photos are safe, but this browser could not preserve their camera orientation. Enable browser storage, then retry the saved-photo check.';
  let source: string | null;
  try { source = localStorage.getItem(orientationKey(sourceId)); }
  catch { throw new Error(unavailable); }
  // Historical sessions without metadata always used original pixels. Persist
  // that explicitly so a later retake cannot default to a front-camera mirror.
  const mirrored = source === 'mirrored';
  if (readOrientation(targetId) === mirrored) return;
  if (!saveOrientation(targetId, mirrored)) throw new Error(unavailable);
}

export function useCamera(onDiscontinuity: () => void, options: CameraOptions = {}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const callbackRef = useRef<number | null>(null);
  const recorder = useRef<FrameWaiter | null>(null);
  const onDiscontinuityRef = useRef(onDiscontinuity);
  onDiscontinuityRef.current = onDiscontinuity;
  const optionsRef = useRef(options); optionsRef.current = options;
  const mirroredRef = useRef(false);
  const [mirrored, updateMirrored] = useState(false);
  const mounted = useRef(true);
  const [status, setStatus] = useState<CameraStatus>('off');
  const [error, setError] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [frameCount, setFrameCount] = useState(0);
  const [countdown, setCountdown] = useState<number | null>(null);
  const countdownWaiter = useRef<{ resolve: (ready: boolean) => void; timer: ReturnType<typeof setTimeout> } | null>(null);
  const generation = useRef(0);

  const cancelCountdown = useCallback(() => {
    const current = countdownWaiter.current;
    if (!current) return;
    clearTimeout(current.timer); countdownWaiter.current = null;
    if (mounted.current) setCountdown(null);
    current.resolve(false);
  }, []);

  const startCountdown = useCallback((): Promise<boolean> => {
    if (countdownWaiter.current || recorder.current) return Promise.resolve(false);
    if (document.hidden || !stream.current?.getVideoTracks().some(track => track.readyState === 'live' && !track.muted) || (videoRef.current?.readyState ?? 0) < 2) {
      return Promise.reject(new Error('The camera is not ready. Keep this tab open and try again.'));
    }
    setCountdown(2);
    return new Promise(resolve => {
      const current = { resolve, timer: setTimeout(() => {
        if (countdownWaiter.current !== current) return;
        setCountdown(1);
        current.timer = setTimeout(() => {
          if (countdownWaiter.current !== current) return;
          countdownWaiter.current = null; setCountdown(null); resolve(true);
        }, 1000);
      }, 1000) };
      countdownWaiter.current = current;
    });
  }, []);

  const takePhoto = useCallback(async (): Promise<Blob> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight || video.readyState < 2) throw new Error('The camera has not delivered a frame yet.');
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 1024 / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Image capture is unavailable in this browser.');
    // Transform the pixels, not just the preview. Detection boxes and target
    // overlays use these saved-image coordinates for both photos and sequences.
    if (mirroredRef.current) { context.translate(canvas.width, 0); context.scale(-1, 1); }
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('The camera frame could not be encoded.')), 'image/jpeg', 0.88));
  }, []);

  const finish = useCallback((interrupted = false, reason?: string) => {
    const current = recorder.current;
    if (!current) return;
    current.stopping = true; current.interrupted ||= interrupted; current.reason ||= reason;
    clearTimeout(current.timer);
    if (current.encoding) {
      current.timer = setTimeout(() => finish(current.interrupted, current.reason), 20); return;
    }
    recorder.current = null;
    if (mounted.current) setRecording(false);
    current.resolve({ blobs: current.frames, timestamps: current.timestamps, interrupted: current.interrupted, reason: current.reason });
  }, []);

  const interrupt = useCallback((message: string) => {
    cancelCountdown();
    finish(true, message);
    if (mounted.current) setError(message);
    onDiscontinuityRef.current();
  }, [cancelCountdown, finish]);

  const stop = useCallback(() => {
    generation.current += 1;
    cancelCountdown();
    finish(true, 'The camera stopped during recording. Record this clip again.');
    if (videoRef.current && callbackRef.current !== null) videoRef.current.cancelVideoFrameCallback?.(callbackRef.current);
    callbackRef.current = null;
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
    if (videoRef.current) { videoRef.current.srcObject = null; videoRef.current.onresize = null; }
    if (mounted.current) setStatus('off');
  }, [cancelCountdown, finish]);

  const setMirrored = useCallback((value: boolean): boolean => {
    const current = optionsRef.current;
    if (countdownWaiter.current || recorder.current || current.orientationLocked || current.hasSavedEvidence || !stream.current) return false;
    if (value === mirroredRef.current) return true;
    if (!saveOrientation(current.sessionId, value)) {
      setError('This browser could not save the camera orientation. Keep the current view, or enable browser storage before starting a new pair.');
      return false;
    }
    mirroredRef.current = value; updateMirrored(value); setError(null);
    onDiscontinuityRef.current();
    return true;
  }, []);

  useEffect(() => {
    stop();
    const saved = readOrientation(options.sessionId) ?? false;
    mirroredRef.current = saved; updateMirrored(saved);
  }, [options.sessionId, stop]);

  const start = useCallback(async () => {
    stop();
    const requestedGeneration = generation.current;
    const requestedSession = optionsRef.current.sessionId;
    setError(null);
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setStatus('error'); setError('Camera access needs HTTPS or localhost. Open this app at a secure address.'); return;
    }
    setStatus('requesting');
    try {
      const next = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } } });
      if (!mounted.current || generation.current !== requestedGeneration) { next.getTracks().forEach(track => track.stop()); return; }
      stream.current = next;
      const video = videoRef.current;
      if (!video) throw new Error('The camera view is unavailable. Try opening the camera again.');
      video.srcObject = next; await video.play();
      if (generation.current !== requestedGeneration) return;
      const track = next.getVideoTracks()[0];
      track.addEventListener('ended', () => { if (stream.current === next) { interrupt('Camera disconnected. Your saved captures are safe.'); setStatus('error'); } });
      track.addEventListener('mute', () => { if (stream.current === next) interrupt('Camera frames were interrupted. Check the scene before continuing.'); });
      const settings = track.getSettings();
      const current = optionsRef.current;
      if (current.sessionId !== requestedSession) { stop(); return; }
      // Existing evidence predates orientation metadata and must retain its
      // original coordinate system. Unknown-facing laptop webcams behave like
      // front cameras; an explicit toggle handles unusual external cameras.
      const saved = readOrientation(current.sessionId);
      let nextMirrored = saved ?? (!current.hasSavedEvidence && settings.facingMode !== 'environment');
      if (!saveOrientation(current.sessionId, nextMirrored) && saved === null) {
        // A choice that cannot survive reload must not create a mirrored first
        // image that would be reopened later with legacy, original coordinates.
        nextMirrored = false;
        setError('Browser storage is unavailable. Using the original camera view so saved captures keep a consistent orientation.');
      }
      mirroredRef.current = nextMirrored; updateMirrored(nextMirrored);
      let previousWidth = video.videoWidth, previousHeight = video.videoHeight;
      setDimensions({ width: previousWidth, height: previousHeight });
      video.onresize = () => {
        if (video.videoWidth !== previousWidth || video.videoHeight !== previousHeight) {
          previousWidth = video.videoWidth; previousHeight = video.videoHeight;
          setDimensions({ width: previousWidth, height: previousHeight });
          interrupt('Camera orientation changed. Restore the saved orientation and check the scene again.');
        }
      };
      let lastMediaTime = -1;
      const frameCallback = async (_now: number, metadata: VideoFrameCallbackMetadata) => {
        if (generation.current !== requestedGeneration || !mounted.current) return;
        if (video.videoWidth && video.videoHeight) setStatus('ready');
        const current = recorder.current;
        const now = performance.now();
        if (current && !current.stopping && !current.encoding && metadata.mediaTime !== lastMediaTime && (!current.frames.length || now - current.started >= current.frames.length * 250 - 10)) {
          if (current.frames.length && now - current.lastFrame > 600) {
            finish(true, 'A gap in the recording makes the action order uncertain. Keep this tab open and try again.');
          } else if (track.getSettings().width !== settings.width || track.getSettings().height !== settings.height) {
            finish(true, 'The camera orientation changed. Keep the camera fixed and record again.');
          } else {
            current.encoding = true;
            const t = current.frames.length ? now - current.started : 0;
            if (!current.frames.length) current.started = now;
            current.lastFrame = now;
            try {
              const blob = await takePhoto();
              current.frames.push(blob); current.timestamps.push(Math.round(t * 100) / 100);
              if (mounted.current) { setElapsed(t / 1000); setFrameCount(current.frames.length); }
            } catch { current.interrupted = true; current.reason = 'A camera frame could not be saved. Please record again.'; }
            current.encoding = false;
            if (current.frames.length >= 48 || current.interrupted) finish(current.interrupted, current.reason);
          }
        }
        lastMediaTime = metadata.mediaTime;
        if (generation.current === requestedGeneration) callbackRef.current = video.requestVideoFrameCallback(frameCallback);
      };
      if (!video.requestVideoFrameCallback) { setStatus('ready'); setError('Still photos are available. Temporal capture needs a browser with video frame callbacks.'); }
      else callbackRef.current = video.requestVideoFrameCallback(frameCallback);
    } catch (cause) {
      if (generation.current !== requestedGeneration || !mounted.current) return;
      const name = cause instanceof DOMException ? cause.name : '';
      setStatus('error');
      setError(name === 'NotAllowedError' ? 'Camera permission was denied. Allow camera access in your browser, then try again.' : name === 'NotFoundError' ? 'No camera was found. Connect one, or explore a replay session.' : name === 'NotReadableError' ? 'The camera is busy. Close other apps using it and try again.' : cause instanceof Error ? cause.message : 'The camera could not start.');
    }
  }, [finish, interrupt, stop, takePhoto]);

  const recordSequence = useCallback((): Promise<Recording> => {
    if (recorder.current) return Promise.reject(new Error('A recording is already in progress.'));
    if (!videoRef.current?.requestVideoFrameCallback) return Promise.reject(new Error('This browser cannot provide frame timing. Use a recent Chrome, Safari, or Firefox.'));
    setRecording(true); setElapsed(0); setFrameCount(0); setError(null);
    return new Promise(resolve => {
      recorder.current = { resolve, started: performance.now(), frames: [], timestamps: [], lastFrame: 0, encoding: false, stopping: false, interrupted: false, timer: setTimeout(() => finish(false), 12000) };
    });
  }, [finish]);

  useEffect(() => {
    mounted.current = true;
    const visibility = () => { if (document.hidden && stream.current) interrupt('Capture paused while this page was hidden. Your saved first capture is safe.'); };
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('offline', cancelCountdown);
    return () => { mounted.current = false; document.removeEventListener('visibilitychange', visibility); window.removeEventListener('offline', cancelCountdown); stop(); };
  }, [cancelCountdown, interrupt, stop]);

  return { videoRef, status, error, dimensions, mirrored, setMirrored, countdown, startCountdown, cancelCountdown, recording, elapsed, frameCount, start, stop, takePhoto, recordSequence, stopRecording: () => finish(false) };
}
