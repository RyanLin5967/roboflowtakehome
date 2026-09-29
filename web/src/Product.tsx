import { useEffect, useState } from 'react';
import CaptureApp from './App';
import Workspace from './Workspace';
import { ProductHeader, Alert } from './Shell';
import { ensureHistoryCapacity, forgetSession, listLocalSessions, readPending, rememberSession, request, saveConnection } from './api';
import type { Connection, LocalSession, Session } from './api';
import { formatDate, jsonPost, messageOf, nameOfRecipe } from './productApi';
import type { InviteInfo } from './productApi';

function Library() {
  const [sessions, setSessions] = useState<LocalSession[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [forgetting, setForgetting] = useState<string | null>(null);
  const refresh = () => listLocalSessions().then(setSessions).catch(cause => setError(messageOf(cause))).finally(() => setLoaded(true));
  useEffect(() => { void refresh(); }, []);
  const open = (session: Connection) => { saveConnection(session); window.location.hash = '/capture'; };
  const forget = async (id: string) => {
    try { await forgetSession(id); setForgetting(null); await refresh(); } catch (cause) { setError(messageOf(cause)); }
  };
  return <div className="app-shell"><ProductHeader current="library"/><Alert message={error} dismiss={() => setError(null)}/><main className="product-main"><div className="page-heading"><div><p className="eyebrow">ON THIS DEVICE</p><h1>Your sessions<span>.</span></h1><p>Pick up where you left off. Your first observation stays with its pair.</p></div><a href="#/capture" className="secondary">Back to capture ↗</a></div>
    {!loaded ? <p className="empty-state">Opening your session library…</p> : sessions.length === 0 ? <div className="empty-state"><h2>No saved sessions yet.</h2><p>Start a capture or join a collection. Sessions you open here will stay in this library.</p><a href="#/capture" className="primary">Make your first pair <span aria-hidden="true">↗</span></a></div> : <div className="local-sessions">{sessions.map(session => <article className="session-card" key={session.id}><div><span className={`tag ${session.mode === 'replay' ? 'fixture' : ''}`}>{session.mode === 'replay' ? 'Test replay' : 'Camera capture'}</span><h2>{session.recipe ? nameOfRecipe(session.recipe) : 'Saved collection'}</h2><p>{session.object_a} + {session.object_b}</p><small>{formatDate(new Date(session.updatedAt).toISOString())} · {session.id.slice(0, 8)}</small></div><div className="session-card-actions"><span className={`tag ${session.state === 'published' ? 'approved' : ''}`}>{session.state === 'published' ? 'Complete pair' : session.state === 'baseline_ready' ? 'First observation saved' : session.state === 'ready' ? 'Ready to save' : 'In progress'}</span><button className="primary" onClick={() => open(session)}>Open session <span aria-hidden="true">↗</span></button>{forgetting === session.id ? <div className="forget-confirm"><p>Forget access on this device? Server evidence is retained. Download it first if you need it.</p><button className="text-button" onClick={() => void forget(session.id)}>Forget access</button><button className="text-button" onClick={() => setForgetting(null)}>Keep it</button></div> : <button className="text-button muted" onClick={() => setForgetting(session.id)}>Forget from this device</button>}</div></article>)}</div>}
    <p className="library-note">Session access is stored only in this browser. Clearing browser storage removes it; exported datasets and collection records remain on the server.</p></main></div>;
}

function Join({ token }: { token: string }) {
  const [info, setInfo] = useState<InviteInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [storageReady, setStorageReady] = useState(false);
  const idempotencyStorageKey = `counterpart.join.${token}`;
  useEffect(() => {
    let current = true;
    request<InviteInfo>('/invites/info', null, jsonPost({ token })).then(value => { if (current) setInfo(value); }).catch(cause => { if (current) setError(messageOf(cause)); });
    ensureHistoryCapacity().then(() => { if (current) setStorageReady(true); }).catch(cause => { if (current) setError(messageOf(cause)); });
    return () => { current = false; };
  }, [token]);
  const join = async () => {
    setBusy(true); setError(null);
    try {
      // The join key survives a lost HTTP response and reload. Tokens never enter request URLs.
      let idempotencyKey = sessionStorage.getItem(idempotencyStorageKey);
      if (!idempotencyKey) { idempotencyKey = crypto.randomUUID(); sessionStorage.setItem(idempotencyStorageKey, idempotencyKey); }
      const result = await request<{ session: Session; token: string }>('/invites/sessions', null, jsonPost({ token, idempotency_key: idempotencyKey }));
      const connection = { id: result.session.id, token: result.token };
      saveConnection(connection); await rememberSession(connection, result.session);
      // Do not discard any other session's queued bytes when changing active sessions.
      await readPending(connection.id);
      sessionStorage.removeItem(idempotencyStorageKey);
      window.location.hash = '/capture';
    } catch (cause) { setError(messageOf(cause)); } finally { setBusy(false); }
  };
  return <div className="app-shell"><ProductHeader current="join"/><Alert message={error}/><main className="invite-landing">{info ? <><p className="eyebrow">YOU'RE INVITED TO COLLECT</p><h1>{info.campaign.name}<span>.</span></h1><p className="join-lead">Two observations. One deliberate change.</p><section className="invite-task"><span className={`tag ${info.campaign.mode === 'replay' ? 'fixture' : ''}`}>{info.campaign.mode === 'replay' ? 'Test replay · no model calls' : 'Real camera · Roboflow verification'}</span><h2>{nameOfRecipe(info.campaign.recipe)}</h2><p>{info.campaign.recipe === 'spatial' ? `Move the ${info.campaign.object_a} across the ${info.campaign.object_b}. Keep the ${info.campaign.object_b} and camera still.` : `Move the ${info.campaign.object_a} and ${info.campaign.object_b} into the target area in opposite orders. Keep the start and finish the same.`}</p><dl><div><dt>You'll need</dt><dd>One {info.campaign.object_a}, one {info.campaign.object_b}{info.campaign.mode === 'live' ? ', and a steady camera.' : '.'}</dd></div><div><dt>Good pairs to go</dt><dd>{info.campaign.progress.remaining_pairs}</dd></div></dl><button className="primary" onClick={() => void join()} disabled={busy || !info.accepting || !storageReady}>{busy ? 'Opening your session…' : !info.accepting ? 'This invitation is closed' : 'Start collecting'}<span aria-hidden="true">↗</span></button><p className="fine-print">Your camera opens only when you choose. Images stay with this collection. {info.campaign.mode === 'live' ? 'Roboflow processes submitted images for verification; dataset upload is a separate operator action.' : 'Replay is labeled test evidence throughout.'}</p></section><p className="fine-print">Invitation expires {formatDate(info.invite.expires_at)}.</p></> : !error ? <p className="empty-state">Opening the collection invitation…</p> : <div className="empty-state"><h1>This invitation could not be opened.</h1><p>Ask the collection owner for a current link.</p></div>}</main></div>;
}

export default function Product() {
  const [route, setRoute] = useState(() => window.location.hash.slice(1));
  useEffect(() => { const update = () => setRoute(window.location.hash.slice(1)); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []);
  if (route.startsWith('/join/')) return <Join token={route.slice(6)} key={route}/>;
  const evidenceRoute = route.match(/^\/workspace\/evidence\/([a-zA-Z0-9-]+)(?:\?(.*))?$/);
  if (evidenceRoute) {
    const selection = new URLSearchParams(evidenceRoute[2] || '');
    return <Workspace key={route} evidenceSessionId={evidenceRoute[1]} initialBaseline={selection.get('baseline') || undefined} initialCounterpart={selection.get('counterpart') || undefined}/>;
  }
  if (route.startsWith('/workspace')) return <Workspace/>;
  if (route === '/library') return <Library/>;
  return <CaptureApp key={route}/>;
}
