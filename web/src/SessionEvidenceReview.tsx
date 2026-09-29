import { useEffect, useState } from 'react';
import type { Session } from './api';
import EvidenceWorkbench from './EvidenceWorkbench';
import { Alert } from './Shell';
import { messageOf, operator } from './productApi';

export default function SessionEvidenceReview({ sessionId, initialBaseline, initialCounterpart }: {
  sessionId: string; initialBaseline?: string; initialCounterpart?: string;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setSession(null); setError(null);
    operator<Session>(`/sessions/${encodeURIComponent(sessionId)}`)
      .then(value => { if (active) setSession(value); })
      .catch(cause => { if (active) setError(messageOf(cause)); });
    return () => { active = false; };
  }, [sessionId]);
  return <main className="product-main">
    <div className="page-heading"><div><p className="eyebrow">REVIEW YOUR SAVED PHOTOS</p><h1>Check the pair.</h1><p>Compare the originals. Record a human decision only if the change and fixed objects are clear.</p></div><a className="secondary" href="#/capture">Back to capture ↗</a></div>
    <Alert message={error}/>
    {session ? <EvidenceWorkbench sessionId={session.id} objectA={session.object_a} objectB={session.object_b} recipe={session.recipe} initialBaseline={initialBaseline} initialCounterpart={initialCounterpart} initialAdjudicationOpen/> : !error && <p className="empty-state">Opening the saved evidence…</p>}
  </main>;
}
