import { useCallback, useEffect, useState } from 'react';
import { Alert, ProductHeader } from './Shell';
import CampaignForm from './CampaignForm';
import CollectionDetail from './CollectionDetail';
import { humanName, jsonPost, messageOf, nameOfRecipe, operator } from './productApi';
import type { Campaign, OperatorSession } from './productApi';

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return <section className="login-card"><p className="eyebrow">COLLECTION WORKSPACE</p><h1>Make the dataset<br/>worth collecting.</h1><p>Set a task, invite collectors, review the evidence, and release complete pairs.</p><form onSubmit={async event => { event.preventDefault(); setBusy(true); setError(null); try { await operator('/login', jsonPost({ token })); setToken(''); onSignedIn(); } catch (cause) { setError(messageOf(cause)); } finally { setBusy(false); } }}><label htmlFor="operator-token">Operator access token</label><input id="operator-token" type="password" value={token} onChange={event => setToken(event.target.value)} autoComplete="off" required/><p className="fine-print">Use the token configured by the server operator. It stays out of browser storage and invitation links.</p><Alert message={error}/><button className="primary" disabled={!token.trim() || busy}>{busy ? 'Signing in…' : 'Open workspace'}<span aria-hidden="true">↗</span></button></form></section>;
}

export default function Workspace() {
  const [auth, setAuth] = useState<OperatorSession | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [current, setCurrent] = useState<Campaign | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const authenticate = useCallback(async () => {
    try { let state = await operator<OperatorSession>('/session'); if (state.local_mode && !state.authenticated) { await operator('/bootstrap', jsonPost({})); state = await operator<OperatorSession>('/session'); } setAuth(state); setError(null); }
    catch (cause) { setError(messageOf(cause)); }
  }, []);
  useEffect(() => { void authenticate(); }, [authenticate]);
  useEffect(() => {
    if (!auth?.authenticated || current) return;
    let active = true; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { try { const result = await operator<{ campaigns: Campaign[] }>('/campaigns'); if (active) { setCampaigns(result.campaigns); setLoaded(true); } } catch (cause) { if (active) setError(messageOf(cause)); } finally { if (active) timer = setTimeout(poll, 5000); } };
    void poll(); return () => { active = false; clearTimeout(timer); };
  }, [auth?.authenticated, current]);
  return <div className="app-shell"><ProductHeader current="workspace"/><Alert message={error} dismiss={() => setError(null)}/>{!auth ? <p className="empty-state">Opening the collection workspace…</p> : !auth.authenticated ? <SignIn onSignedIn={() => void authenticate()}/> : <>{current ? <CollectionDetail key={current.id} initial={current} onBack={() => setCurrent(null)}/> : <main className="product-main"><div className="page-heading"><div><p className="eyebrow">THE COLLECTION WORKSPACE</p><h1>Build the difference<span>.</span></h1><p>Define the change. Collect together. Keep the pairs that hold up.</p></div><button className="primary compact-button" onClick={() => setCreating(true)}>New collection <span aria-hidden="true">+</span></button></div>{!loaded ? <p className="empty-state">Loading collections…</p> : campaigns.length ? <div className="campaign-grid">{campaigns.map(campaign => <button className="campaign-card" key={campaign.id} onClick={() => setCurrent(campaign)}><div className="campaign-card-top"><span className="eyebrow">{nameOfRecipe(campaign.recipe).toUpperCase()}</span><span aria-hidden="true">↗</span></div><h2>{campaign.name}</h2><p>{humanName(campaign.object_a)} + {campaign.object_b}</p><div className="campaign-progress"><strong>{campaign.progress.approved_pairs}<span> / {campaign.target_pairs}</span></strong><span>approved pairs</span><progress value={campaign.progress.approved_pairs} max={campaign.target_pairs}/></div><div className="campaign-card-bottom"><span>{campaign.progress.pending_review_pairs} awaiting review</span><span className={`tag ${campaign.mode === 'replay' ? 'fixture' : ''}`}>{campaign.mode === 'replay' ? 'Test replay' : 'Live camera'}</span></div></button>)}</div> : <div className="workspace-empty"><div className="empty-pair-symbol" aria-hidden="true"><span>A</span><span>⇄</span><span>B</span></div><h2>A useful difference<br/>starts with a clear task.</h2><p>Create a collection, invite someone to capture it, and review both sides before the data goes anywhere.</p><button className="primary compact-button" onClick={() => setCreating(true)}>Create your first collection <span aria-hidden="true">↗</span></button></div>}</main>}<footer className="workspace-footer"><span>{auth.local_mode ? 'Local operator workspace' : 'Operator session active'} · private session access</span><button className="text-button" onClick={async () => { try { await operator('/logout', jsonPost({})); setAuth({ ...auth, authenticated: false }); setCurrent(null); } catch (cause) { setError(messageOf(cause)); } }}>Sign out</button></footer>{creating && <CampaignForm onClose={() => setCreating(false)} onCreated={campaign => { setCreating(false); setCurrent(campaign); }}/>}</>}</div>;
}
