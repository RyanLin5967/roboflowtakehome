import { openDB } from 'idb';

export type Recipe = 'spatial' | 'temporal';
export type Mode = 'live' | 'replay';
export type Slot = 'baseline' | 'counterpart' | 'recovery' | 'preview';
export type Scenario = 'baseline' | 'counterpart' | 'wrong_relation' | 'camera_moved' | 'ambiguous' | 'recovery';
export type Frame = { asset_id: string; t_ms: number; width: number; height: number; detections?: Array<{ label: string; confidence: number; box: [number, number, number, number] }>; model?: { provider?: string; model_id?: string } };
export type Check = { name: string; passed: boolean; detail?: string };
export type Result = { accepted: boolean; code: string; message: string; checks: Check[]; evidence: Record<string, unknown>; verifier_version: string };
export type Capture = { id: string; slot: Slot; status: string; revision: number; epoch: number; created_at: string; frames: Frame[]; result: Result | null; error: string | null };
export type Session = {
  id: string; recipe: Recipe; mode: Mode; object_a: string; object_b: string;
  revision: number; epoch: number; state: 'collecting' | 'baseline_ready' | 'ready' | 'published';
  recovery_required: boolean; baseline: Capture | null; counterpart: Capture | null;
  latest_capture: Capture | null; feedback: Capture | Result | null;
  pair: Record<string, unknown> | null; events: Array<Record<string, unknown>>;
};
export type Connection = { id: string; token: string };
export type Metadata = { slot: Slot; expected_revision: number; epoch: number; client_seq: number; idempotency_key: string; timestamps_ms: number[]; interrupted: boolean };
export type PendingCapture = { sessionId: string; metadata: Metadata; blobs: Blob[]; createdAt: number };
export const CONNECTION_KEY = 'counterpart.session.v1';

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message); this.status = status; this.code = code;
  }
}

export async function request<T>(path: string, connection?: Connection | null, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (connection) headers.set('Authorization', `Bearer ${connection.token}`);
  if (typeof options.body === 'string') headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, { ...options, headers, cache: 'no-store' });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.detail;
    throw new ApiError(response.status, detail && typeof detail === 'object' ? detail.code : 'request_failed',
      detail && typeof detail === 'object' && typeof detail.message === 'string' ? detail.message :
        typeof detail === 'string' ? detail : `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export function loadConnection(): Connection | null {
  try {
    const value = JSON.parse(localStorage.getItem(CONNECTION_KEY) || 'null');
    return value && typeof value.id === 'string' && typeof value.token === 'string' ? value : null;
  } catch { return null; }
}
export function saveConnection(value: Connection | null) {
  if (value) localStorage.setItem(CONNECTION_KEY, JSON.stringify(value));
  else localStorage.removeItem(CONNECTION_KEY);
}

const database = () => openDB('counterpart-capture', 1, {
  upgrade(db) { db.createObjectStore('pending', { keyPath: 'sessionId' }); },
});
export async function savePending(value: PendingCapture) {
  if (value.blobs.length > 48 || value.blobs.reduce((total, blob) => total + blob.size, 0) > 32 * 1024 * 1024) {
    throw new Error('This capture is too large to keep safely. Use a shorter sequence.');
  }
  const db = await database();
  await db.put('pending', value);
  db.close();
}
export async function readPending(sessionId: string): Promise<PendingCapture | undefined> {
  const db = await database(); const result = await db.get('pending', sessionId); db.close(); return result;
}
export async function clearPending(sessionId: string) {
  const db = await database(); await db.delete('pending', sessionId); db.close();
}
export async function submitPending(connection: Connection, pending: PendingCapture) {
  const body = new FormData();
  body.set('metadata', JSON.stringify(pending.metadata));
  pending.blobs.forEach((blob, i) => body.append('frames', blob, `frame-${String(i).padStart(3, '0')}.jpg`));
  return request<{ capture: Capture; session: Session }>(`/sessions/${connection.id}/captures`, connection, { method: 'POST', body });
}
export async function authorizedBlob(connection: Connection, path: string): Promise<Blob> {
  const response = await fetch(`/api/sessions/${connection.id}/${path}`, { headers: { Authorization: `Bearer ${connection.token}` }, cache: 'no-store' });
  if (!response.ok) throw new Error(`Could not load saved media (${response.status}).`);
  return response.blob();
}
export async function download(connection: Connection, kind: 'manifest' | 'archive') {
  const blob = await authorizedBlob(connection, kind);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url;
  link.download = `counterpart-${connection.id.slice(0, 8)}.${kind === 'archive' ? 'zip' : 'json'}`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
