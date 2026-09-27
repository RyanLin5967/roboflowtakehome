import { openDB } from 'idb';

export type Recipe = 'spatial' | 'temporal';
export type Mode = 'live' | 'replay';
export type Slot = 'baseline' | 'counterpart' | 'recovery' | 'preview';
export type Scenario = 'baseline' | 'counterpart' | 'wrong_relation' | 'camera_moved' | 'ambiguous' | 'recovery';
export type Frame = { asset_id: string; t_ms: number; width: number; height: number; detections?: Array<{ label: string; confidence: number; box: [number, number, number, number] }>; model?: { provider?: string; model_id?: string } };
export type Check = { name: string; passed: boolean; detail?: string; outcome?: 'pass' | 'fail' | 'unknown'; code?: string; measurements?: Record<string, unknown> };
export type Result = { outcome?: 'pass' | 'fail' | 'unknown'; hard_error?: boolean; accepted: boolean; code: string; message: string; checks: Check[]; evidence: Record<string, unknown>; verifier_version: string };
export type Capture = { id: string; slot: Slot; status: string; revision: number; epoch: number; created_at: string; frames: Frame[]; result: Result | null; error: string | null; progress?: { completed_frames: number; total_frames: number } };
export type Session = {
  id: string; recipe: Recipe; mode: Mode; object_a: string; object_b: string;
  revision: number; epoch: number; state: 'collecting' | 'baseline_ready' | 'ready' | 'published';
  human_review_required?: boolean; baseline_authorization?: Record<string, unknown> | null;
  recovery_required: boolean; baseline: Capture | null; counterpart: Capture | null;
  latest_capture: Capture | null; feedback: Capture | Result | null;
  pair: Record<string, unknown> | null; events: Array<Record<string, unknown>>;
  review?: { status: 'pending' | 'approved' | 'rejected' | 'needs_revision'; version: number; reason?: string; updated_at?: string };
  campaign_id?: string | null; recipe_snapshot?: Record<string, unknown>;
  progress?: { completed_frames: number; total_frames: number };
  created_at?: string;
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

const database = () => openDB('counterpart-capture', 2, {
  upgrade(db) {
    if (!db.objectStoreNames.contains('pending')) db.createObjectStore('pending', { keyPath: 'sessionId' });
    if (!db.objectStoreNames.contains('sessions')) db.createObjectStore('sessions', { keyPath: 'id' });
  },
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

export type LocalSession = Connection & { recipe?: Recipe; mode?: Mode; state?: string; object_a?: string; object_b?: string; campaign_id?: string | null; updatedAt: number; unavailable?: boolean };
const HISTORY_LIMIT = 256;
export async function listLocalSessions(): Promise<LocalSession[]> {
  const db = await database();
  try {
    const active = loadConnection();
    if (active && !await db.get('sessions', active.id)) await db.put('sessions', { ...active, updatedAt: Date.now() });
    return (await db.getAll('sessions')).sort((a, b) => b.updatedAt - a.updatedAt); } finally { db.close(); }
}
export async function ensureHistoryCapacity() {
  const db = await database();
  try { if (await db.count('sessions') >= HISTORY_LIMIT) throw new Error('This device has 256 saved sessions. Download and forget an old session from Your sessions before starting another.'); } finally { db.close(); }
}
export async function rememberSession(connection: Connection, session: Session) {
  const db = await database();
  try {
    const existing = await db.get('sessions', connection.id);
    if (!existing && await db.count('sessions') >= HISTORY_LIMIT) throw new Error('Your session library is full. Keep this page open and download your evidence.');
    await db.put('sessions', { ...connection, recipe: session.recipe, mode: session.mode, state: session.state, object_a: session.object_a, object_b: session.object_b, campaign_id: session.campaign_id, updatedAt: existing?.state === session.state ? existing.updatedAt : Date.now() });
  } finally { db.close(); }
}
export async function forgetSession(id: string) {
  const db = await database();
  try {
    if (await db.get('pending', id)) throw new Error('This session has an unconfirmed capture on this device. Open it and resolve the saved upload before forgetting its access.');
    await db.delete('sessions', id);
    if (loadConnection()?.id === id) saveConnection(null);
  } finally { db.close(); }
}
export async function operatorBlob(path: string): Promise<Blob> {
  const response = await fetch(`/api/operator${path}`, { cache: 'no-store', credentials: 'same-origin' });
  if (!response.ok) throw new Error(`Could not load evidence (${response.status}).`);
  return response.blob();
}
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
