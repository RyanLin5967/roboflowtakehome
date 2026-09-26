import { useCallback, useEffect, useRef, useState } from 'react';

export type Recording = { blobs: Blob[]; timestamps: number[]; interrupted: boolean; reason?: string };
export type CameraStatus = 'off' | 'requesting' | 'ready' | 'error';
type FrameWaiter = { resolve: (value: Recording) => void; started: number; frames: Blob[]; timestamps: number[]; lastFrame: number; encoding: boolean; stopping: boolean; interrupted: boolean; reason?: string; timer: ReturnType<typeof setTimeout> };

export function useCamera(onDiscontinuity: () => void) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const callbackRef = useRef<number | null>(null);
  const recorder = useRef<FrameWaiter | null>(null);
  const onDiscontinuityRef = useRef(onDiscontinuity);
  onDiscontinuityRef.current = onDiscontinuity;
  const mounted = useRef(true);
  const [status, setStatus] = useState<CameraStatus>('off');
  const [error, setError] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [frameCount, setFrameCount] = useState(0);
  const generation = useRef(0);

  const takePhoto = useCallback(async (): Promise<Blob> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight || video.readyState < 2) throw new Error('The camera has not delivered a frame yet.');
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 1024 / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Image capture is unavailable in this browser.');
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
    finish(true, message);
    if (mounted.current) setError(message);
    onDiscontinuityRef.current();
  }, [finish]);

  const stop = useCallback(() => {
    generation.current += 1;
    finish(true, 'The camera stopped during recording. Record this clip again.');
    if (videoRef.current && callbackRef.current !== null) videoRef.current.cancelVideoFrameCallback?.(callbackRef.current);
    callbackRef.current = null;
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
    if (videoRef.current) { videoRef.current.srcObject = null; videoRef.current.onresize = null; }
    if (mounted.current) setStatus('off');
  }, [finish]);

  const start = useCallback(async () => {
    stop();
    const requestedGeneration = generation.current;
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
    return () => { mounted.current = false; document.removeEventListener('visibilitychange', visibility); stop(); };
  }, [interrupt, stop]);

  return { videoRef, status, error, dimensions, recording, elapsed, frameCount, start, stop, takePhoto, recordSequence, stopRecording: () => finish(false) };
}
