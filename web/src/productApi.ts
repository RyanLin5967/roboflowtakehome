import { request } from './api';
import type { Capture, Mode, Recipe } from './api';

export type Campaign = {
  id: string; name: string; active?: boolean; recipe: Recipe; mode: Mode; object_a: string; object_b: string;
  recipe_snapshot: Record<string, unknown>; target_pairs: number; created_at: string;
  progress: { sessions: number; published_pairs: number; approved_pairs: number; rejected_pairs: number; needs_revision_pairs: number; pending_review_pairs: number; target_pairs: number; remaining_pairs: number };
};
export type ReviewStatus = 'pending' | 'approved' | 'rejected' | 'needs_revision';
export type Review = { status: ReviewStatus; version: number; reviewer?: string; reason?: string; updated_at?: string };
export type PairManifest = { schema_version: string; pair_id: string; session_id: string; recipe: Recipe; mode: Mode; objects: { a: string; b: string }; baseline: Capture; counterpart: Capture; split: string; provenance: { evidence_kind: string; human_reviewed: boolean } };
export type PairRecord = { id: string; session_id: string; manifest: PairManifest; created_at: string; export_status: string; review: Review };
export type CollectionSession = { id: string; recipe: Recipe; mode: Mode; object_a: string; object_b: string; state: string; revision: number; epoch: number; created_at: string; pair_id: string | null; review_status: ReviewStatus | null; review_version: number; capture_count: number; failed_capture_count: number };
export type Invite = { id: string; campaign_id: string; label: string; expires_at: string; max_sessions: number; sessions_created: number; revoked_at: string | null; token?: string };
export type Release = { id: string; campaign_id: string; schema_version: string; created_at: string; pair_count: number; split_counts: { train: number; valid: number; test: number }; pairs?: Array<{ manifest: PairManifest; review: Review }> };
export type Destination = { enabled: boolean; reason?: string; workspace?: string; project?: string; privacy?: string };
export type InviteInfo = { campaign: Campaign; invite: { id: string; label: string; expires_at: string; remaining_sessions: number }; accepting: boolean };
export type OperatorSession = { authenticated: boolean; local_mode: boolean };
export const operator = <T,>(path: string, options: RequestInit = {}) => request<T>(`/operator${path}`, null, { credentials: 'same-origin', ...options });
export const jsonPost = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });
export const nameOfRecipe = (recipe: Recipe) => recipe === 'spatial' ? 'Switch sides' : 'Reverse arrival order';
export const reviewName = (status: ReviewStatus) => ({ pending: 'Awaiting review', approved: 'Approved', rejected: 'Rejected', needs_revision: 'Needs revision' })[status];
export const humanName = (name: string) => name.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase());
export const formatDate = (date?: string) => date ? new Date(date).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
export const messageOf = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';
