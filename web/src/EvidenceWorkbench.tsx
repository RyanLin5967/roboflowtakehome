import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, operatorBlob } from './api';
import type { Capture, Result } from './api';
import { EvidenceMedia } from './Evidence';
import RecordedChecks from './RecordedChecks';
import { Alert } from './Shell';
import { formatDate, humanName, jsonPost, messageOf, operator } from './productApi';

export type DecisionRevision = {
  id: string; version: number; created_at: string; recipe_snapshot: { recipe_version: string };
  result: Result; reference_result?: Result | null; evidence_sha256: string; verifier_version?: string;
  comparison?: { status: 'evaluated' | 'blocked_reference_unknown' | 'blocked_reference_failure'; outcome: 'pass' | 'unknown' | 'fail'; candidate_checks: 'complete' | 'dependency_gate_only'; reference_capture_id: string | null; explanation: string };
};
export type SavedEvidence = { capture: Capture; evidence_sha256: string; revision_version: number; revisions: DecisionRevision[]; review_version: number; reviews: Array<{ id: string; version: number; decision: string; reason: string; created_at: string }>; review_eligibility: { allowed: boolean; code: string; message: string }; baseline_authorization_eligibility?: { allowed: boolean; code: string; message: string } };
export type EvidenceRecord = {
  session_id: string; session_revision: number; captures: SavedEvidence[]; adjudications: Array<{ id: string; created_at: string; reason: string; pair_id?: string }>;
  baseline_authorization?: { id: string; reason: string; created_at: string } | null; baseline_authorization_criteria?: Array<{ id: string; label: string }>;
  adjudication_criteria: Array<{ id: string; label: string }>; recipe_versions: string[];
  selected_pair?: { baseline_id: string; counterpart_id: string; evidence_sha256: string } | null;
};

function RevisionHistory({ evidence, sessionId, onUpdated }: { evidence: SavedEvidence; sessionId: string; onUpdated: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [version, setVersion] = useState('');
  const [versions, setVersions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef<{ fingerprint: string; key: string } | null>(null);
  useEffect(() => { if (open) void operator<EvidenceRecord>(`/sessions/${sessionId}/evidence`).then(record => { setVersions(record.recipe_versions); setVersion(current => current || record.recipe_versions.at(-1) || ''); }).catch(cause => setError(messageOf(cause))); }, [open, sessionId]);
  const reverify = async () => {
    setBusy(true); setError(null);
    const fingerprint = `${evidence.capture.id}:${version}:${evidence.revision_version}:${evidence.evidence_sha256}`;
    if (operation.current?.fingerprint !== fingerprint) operation.current = { fingerprint, key: crypto.randomUUID() };
    try {
      await operator(`/sessions/${sessionId}/captures/${evidence.capture.id}/reverify`, jsonPost({ expected_version: evidence.revision_version, expected_evidence_sha256: evidence.evidence_sha256, recipe_version: version, idempotency_key: operation.current.key }));
      operation.current = null; await onUpdated();
    } catch (cause) { if (cause instanceof ApiError && cause.status === 409) { try { await onUpdated(); } catch { /* Keep the original conflict visible. */ } } setError(messageOf(cause)); }
    finally { setBusy(false); }
  };
  return <details className="decision-history" onToggle={event => setOpen(event.currentTarget.open)}><summary>Decision history · {evidence.revisions.length} {evidence.revisions.length === 1 ? 'revision' : 'revisions'}</summary><p>Recheck these saved observations with another verifier. The original decision and published dataset remain unchanged.</p><div className="revision-list"><article><span className="eyebrow">ORIGINAL DECISION</span><strong>{evidence.capture.result?.message || evidence.capture.error || humanName(evidence.capture.status)}</strong><small>{evidence.capture.result?.verifier_version || 'No verifier result'} · {formatDate(evidence.capture.created_at)}</small></article>{evidence.revisions.map(revision => {
      const blocked = revision.comparison?.candidate_checks === 'dependency_gate_only';
      return <article key={revision.id}>
        <span className="eyebrow">REVISION {revision.version}</span>
        {revision.comparison && <p><span className={`tag ${revision.comparison.outcome === 'pass' ? 'approved' : revision.comparison.outcome === 'fail' ? 'rejected' : 'needs_revision'}`}>Comparison outcome: {humanName(revision.comparison.outcome)}</span></p>}
        <strong>{blocked ? revision.comparison!.explanation : revision.result.message}</strong>
        <small>{revision.recipe_snapshot.recipe_version} · {revision.result.verifier_version} · {formatDate(revision.created_at)}</small>
        {blocked && <p className="fine-print">The revised baseline did not pass, so this counterpart reached only the verifier's prerequisite check. Its full comparison was not evaluated.</p>}
        {revision.reference_result && <details><summary>Revised baseline checks</summary><p>{revision.reference_result.message}</p><RecordedChecks checks={revision.reference_result.checks}/></details>}
        <details><summary>{blocked ? 'Recorded counterpart response' : 'Recorded checks'}</summary>{blocked && <p>{revision.result.message}</p>}<RecordedChecks checks={revision.result.checks}/></details>
      </article>;
    })}</div><form className="reverify-form" onSubmit={event => { event.preventDefault(); void reverify(); }}><label>Verifier recipe version<select aria-label={`Verifier version for ${evidence.capture.id}`} value={version} onChange={event => setVersion(event.target.value)}>{versions.map(item => <option value={item} key={item}>{item}</option>)}</select></label><button className="secondary" disabled={busy || !version}>{busy ? 'Rechecking…' : 'Append verification revision'}</button></form><Alert message={error}/></details>;
}

function AttemptDecision({ evidence, sessionId, onUpdated }: { evidence: SavedEvidence; sessionId: string; onUpdated: () => Promise<void> }) {
  const [decision, setDecision] = useState('confirmed_uncertain');
  const [seenVersion, setSeenVersion] = useState(evidence.review_version);
  const reviewChanged = seenVersion !== evidence.review_version;
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef<{ fingerprint: string; key: string } | null>(null);
  const submit = async () => {
    const body = { decision, reason: note.trim(), expected_version: seenVersion, expected_evidence_sha256: evidence.evidence_sha256 };
    const fingerprint = JSON.stringify(body);
    if (operation.current?.fingerprint !== fingerprint) operation.current = { fingerprint, key: crypto.randomUUID() };
    setBusy(true); setError(null);
    try { await operator(`/sessions/${sessionId}/captures/${evidence.capture.id}/review`, jsonPost({ ...body, idempotency_key: operation.current.key })); operation.current = null; setSeenVersion(seenVersion + 1); await onUpdated(); setNote(''); }
    catch (cause) { if (cause instanceof ApiError && cause.status === 409) { try { await onUpdated(); } catch { /* Keep the original conflict visible. */ } } setError(messageOf(cause)); }
    finally { setBusy(false); }
  };
  return <details className="attempt-decision"><summary>Record an attempt review{evidence.reviews.length ? ` · ${evidence.reviews.length} saved` : ''}</summary><p>This records why an attempt remains unresolved or needs another capture. It cannot approve a dataset pair.</p>{evidence.reviews.map(item => <div className="last-review" key={item.id}><strong>{humanName(item.decision)}</strong><p>{item.reason}</p><small>{formatDate(item.created_at)} · revision {item.version}</small></div>)}<form onSubmit={event => { event.preventDefault(); void submit(); }}><label>Attempt decision<select aria-label={`Attempt decision for ${evidence.capture.id}`} value={decision} onChange={event => setDecision(event.target.value)}><option value="confirmed_uncertain">Still uncertain</option><option value="needs_recapture">Needs another capture</option><option value="rejected">Reject this attempt</option></select></label><label>Reason<textarea aria-label={`Attempt review note for ${evidence.capture.id}`} value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></label><Alert message={error}/>{reviewChanged && <div className="inline-warning"><p>A newer attempt review is shown above. Read it before recording another decision.</p><button className="secondary" type="button" onClick={() => setSeenVersion(evidence.review_version)}>I have read the latest attempt review</button></div>}<button className="secondary" disabled={busy || reviewChanged || !note.trim()}>{busy ? 'Saving…' : 'Save attempt review'}</button></form></details>;
}

function BaselineAuthorization({ evidence, record, sessionId, choices, onUpdated }: { evidence: SavedEvidence; record: EvidenceRecord; sessionId: string; choices: Array<{ value: string; label: string }>; onUpdated: () => Promise<void> }) {
  const [answer, setAnswer] = useState('');
  const [criteria, setCriteria] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef<{ fingerprint: string; key: string } | null>(null);
  const loadAsset = useCallback((assetId: string) => operatorBlob(`/sessions/${sessionId}/assets/${encodeURIComponent(assetId)}`), [sessionId]);
  const required = record.baseline_authorization_criteria || [];
  const canAuthorize = answer && reason.trim().length >= 20 && required.length > 0 && required.every(item => criteria[item.id]);
  const submit = async () => {
    if (!canAuthorize) return;
    const body = { expected_session_revision: record.session_revision, expected_evidence_sha256: evidence.evidence_sha256, answer, criteria, reason: reason.trim() };
    const fingerprint = JSON.stringify(body);
    if (operation.current?.fingerprint !== fingerprint) operation.current = { fingerprint, key: crypto.randomUUID() };
    setBusy(true); setError(null);
    try { await operator(`/sessions/${sessionId}/captures/${evidence.capture.id}/authorize-baseline`, jsonPost({ ...body, idempotency_key: operation.current.key })); operation.current = null; await onUpdated(); }
    catch (cause) { if (cause instanceof ApiError && cause.status === 409) { setCriteria({}); try { await onUpdated(); } catch { /* Preserve the conflict message. */ } } setError(messageOf(cause)); }
    finally { setBusy(false); }
  };
  return <details className="baseline-authorization"><summary>Review this baseline so the collector can continue</summary><p>The visual controls are uncertain. If you can establish the initial relation and review its controls from the original image, authorize collection of a counterpart. This never changes the machine verdict or approves a pair.</p><EvidenceMedia capture={evidence.capture} scope={`operator-session:${sessionId}`} loadAsset={loadAsset} labels={[]}/><form onSubmit={event => { event.preventDefault(); void submit(); }}><label>Initial observation shows<select aria-label="Baseline authorization answer" value={answer} onChange={event => setAnswer(event.target.value)}><option value="">Cannot determine / not reviewed</option>{choices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</select></label><fieldset><legend>Establish the baseline from its original evidence</legend>{required.map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" checked={Boolean(criteria[item.id])} onChange={event => setCriteria(current => ({ ...current, [item.id]: event.target.checked }))}/>{item.label}</label>)}</fieldset><label>Baseline authorization note<textarea aria-label="Baseline authorization note" value={reason} maxLength={2000} minLength={20} onChange={event => setReason(event.target.value)}/></label><p className="fine-print">The final pair will still require human adjudication and ordinary dataset review.</p><Alert message={error}/><button className="primary" disabled={busy || !canAuthorize}>{busy ? 'Recording authorization…' : 'Authorize counterpart capture'}</button></form></details>;
}

export default function EvidenceWorkbench({ sessionId, objectA, objectB, recipe, onUpdated, initialBaseline = '', initialCounterpart = '', initialAdjudicationOpen = false }: { sessionId: string; objectA: string; objectB: string; recipe: 'spatial' | 'temporal'; onUpdated?: () => Promise<void>; initialBaseline?: string; initialCounterpart?: string; initialAdjudicationOpen?: boolean }) {
  const [record, setRecord] = useState<EvidenceRecord | null>(null);
  const [baseline, setBaseline] = useState(initialBaseline);
  const [counterpart, setCounterpart] = useState(initialCounterpart);
  const [adjudicationOpen, setAdjudicationOpen] = useState(initialAdjudicationOpen);
  const [criteria, setCriteria] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState('');
  const [firstLabel, setFirstLabel] = useState('');
  const [secondLabel, setSecondLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const operation = useRef<{ fingerprint: string; key: string } | null>(null);
  const requestGeneration = useRef(0);
  const [loadedSelection, setLoadedSelection] = useState('');
  const loadAsset = useCallback((assetId: string) => operatorBlob(`/sessions/${sessionId}/assets/${encodeURIComponent(assetId)}`), [sessionId]);
  const refresh = useCallback(async () => {
    const generation = ++requestGeneration.current;
    const query = baseline && counterpart ? `?baseline_id=${encodeURIComponent(baseline)}&counterpart_id=${encodeURIComponent(counterpart)}` : '';
    const value = await operator<EvidenceRecord>(`/sessions/${sessionId}/evidence${query}`);
    if (generation === requestGeneration.current) { setRecord(value); setLoadedSelection(`${baseline}:${counterpart}`); }
  }, [sessionId, baseline, counterpart]);
  useEffect(() => { void refresh().catch(cause => setError(messageOf(cause))); return () => { requestGeneration.current++; }; }, [refresh]);
  useEffect(() => { setCriteria({}); setReason(''); setFirstLabel(''); setSecondLabel(''); setNotice(null); }, [baseline, counterpart]);
  const choices = recipe === 'spatial' ? [{ value: 'left', label: `${humanName(objectA)} is left of ${objectB}` }, { value: 'right', label: `${humanName(objectA)} is right of ${objectB}` }] : [{ value: objectA, label: `${humanName(objectA)} arrives first` }, { value: objectB, label: `${humanName(objectB)} arrives first` }];
  const baselineRecord = record?.captures.find(item => item.capture.id === baseline);
  const counterpartRecord = record?.captures.find(item => item.capture.id === counterpart);
  const selected = [baselineRecord, counterpartRecord].filter((item): item is SavedEvidence => Boolean(item));
  const canReview = loadedSelection === `${baseline}:${counterpart}` && selected.length === 2 && selected.every(item => item.review_eligibility.allowed) && record?.selected_pair?.baseline_id === baseline && record?.selected_pair?.counterpart_id === counterpart && record!.adjudication_criteria.every(item => criteria[item.id]) && firstLabel && secondLabel && firstLabel !== secondLabel && reason.trim().length >= 20;
  const adjudicate = async () => {
    if (!record || !canReview) return;
    setBusy(true); setError(null); setNotice(null);
    const body = { baseline_id: baseline, counterpart_id: counterpart, expected_session_revision: record.session_revision, expected_evidence_sha256: record.selected_pair?.evidence_sha256, labels: { baseline: firstLabel, counterpart: secondLabel }, criteria, reason: reason.trim() };
    const fingerprint = JSON.stringify(body);
    if (operation.current?.fingerprint !== fingerprint) operation.current = { fingerprint, key: crypto.randomUUID() };
    try { await operator(`/sessions/${sessionId}/adjudications`, jsonPost({ ...body, idempotency_key: operation.current.key })); operation.current = null; await refresh(); await onUpdated?.(); setNotice('Human decision recorded. The new pair awaits ordinary pair review; its original machine decisions are preserved.'); }
    catch (cause) { if (cause instanceof ApiError && cause.status === 409) { setCriteria({}); try { await refresh(); } catch { /* Preserve the original conflict. */ } } setError(messageOf(cause)); }
    finally { setBusy(false); }
  };
  return <section className="evidence-workbench" aria-label="Saved evidence decisions"><div className="section-lead"><h3>Resolve uncertainty from the originals.</h3><p>Review complete saved observations or compare verifier revisions. A human decision is recorded separately from the machine result.</p><button className="text-button" disabled={busy} onClick={() => { setError(null); void refresh().catch(cause => setError(messageOf(cause))); }}>Reload saved evidence</button></div><Alert message={error}/>{notice && <p className="global-message notice" role="status">{notice}</p>}{!record ? <p>Loading saved decision history…</p> : <>{record.baseline_authorization && <div className="human-provenance"><strong>Counterpart capture authorized by human review</strong><p>{record.baseline_authorization.reason}</p><p>The original baseline machine decision is unchanged. Final pair adjudication remains required.</p></div>}<details open={!initialAdjudicationOpen}><summary>Saved attempts and machine decisions ({record.captures.length})</summary><div className="evidence-decision-list">{record.captures.map(item => <article key={item.capture.id}><div className="panel-heading"><div><strong>{humanName(item.capture.slot)} · {item.capture.id.slice(0, 8)}</strong><p>{item.capture.result?.message || item.capture.error || humanName(item.capture.status)}</p></div><span className={`tag ${item.review_eligibility.allowed ? 'needs_revision' : ''}`}>{item.review_eligibility.allowed ? 'Can be reviewed' : 'Recorded evidence'}</span></div><details className="original-checks"><summary>Original machine checks</summary><RecordedChecks checks={item.capture.result?.checks || []}/></details><AttemptDecision evidence={item} sessionId={sessionId} onUpdated={refresh}/><RevisionHistory evidence={item} sessionId={sessionId} onUpdated={refresh}/>{!record.baseline_authorization && item.baseline_authorization_eligibility?.allowed && <BaselineAuthorization evidence={item} record={record} sessionId={sessionId} choices={choices} onUpdated={async () => { await refresh(); await onUpdated?.(); }}/>}</article>)}</div></details><details className="adjudication-panel" open={adjudicationOpen} onToggle={event => setAdjudicationOpen(event.currentTarget.open)}><summary>Make a human decision on an uncertain pair</summary><p>Choose two complete captures. Confirm only what you can establish from the original evidence. Leave this unresolved if the change or controls cannot be judged.</p><div className="form-two"><label>First observation<select aria-label="First observation for human review" value={baseline} onChange={event => setBaseline(event.target.value)}><option value="">Select saved baseline</option>{record.captures.filter(item => item.capture.slot === 'baseline').map(item => <option value={item.capture.id} key={item.capture.id}>{item.capture.id.slice(0, 8)} · {humanName(item.capture.status)}</option>)}</select></label><label>Counterpart observation<select aria-label="Counterpart observation for human review" value={counterpart} onChange={event => setCounterpart(event.target.value)}><option value="">Select saved counterpart</option>{record.captures.filter(item => item.capture.id !== baseline && (item.capture.slot === 'counterpart' || item.capture.slot === 'baseline')).map(item => <option value={item.capture.id} key={item.capture.id}>{item.capture.id.slice(0, 8)} · {humanName(item.capture.status)}</option>)}</select></label></div>{selected.length === 2 && <div className="review-pair-grid">{selected.map((item, index) => <div key={item.capture.id}><div className="pair-label"><strong>{humanName(item.capture.slot)}</strong><span>ORIGINAL MEDIA</span></div><EvidenceMedia capture={item.capture} scope={`operator-session:${sessionId}`} loadAsset={loadAsset} labels={[]}/><p className="evidence-caption">{item.review_eligibility.message}</p><label>{index === 0 ? 'First observation shows' : 'Counterpart shows'}<select aria-label={index === 0 ? 'First observation label' : 'Counterpart label'} value={index === 0 ? firstLabel : secondLabel} onChange={event => index === 0 ? setFirstLabel(event.target.value) : setSecondLabel(event.target.value)}><option value="">Cannot determine / not reviewed</option>{choices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</select></label></div>)}</div>}{selected.length === 2 && <form className="adjudication-form" onSubmit={event => { event.preventDefault(); void adjudicate(); }}><fieldset><legend>Confirm the evidence, not the expected answer</legend>{record.adjudication_criteria.map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" checked={Boolean(criteria[item.id])} onChange={event => setCriteria(current => ({ ...current, [item.id]: event.target.checked }))}/>{item.label}</label>)}</fieldset><label>Human decision note<textarea aria-label="Human decision note" value={reason} onChange={event => setReason(event.target.value)} minLength={20} maxLength={2000} placeholder="Describe what establishes the change and which controls you checked."/></label><p className="fine-print">At least 20 characters. This records a human adjudication; it never changes the original machine result.</p><button className="primary" disabled={busy || !canReview}>{busy ? 'Recording decision…' : 'Record human decision'}</button></form>}</details>{record.adjudications.length > 0 && <details className="decision-history"><summary>Previous human decisions ({record.adjudications.length})</summary>{record.adjudications.map(item => <article className="last-review" key={item.id}><strong>{formatDate(item.created_at)}</strong><p>{item.reason}</p>{item.pair_id && <small>Pair {item.pair_id.slice(0, 8)}</small>}</article>)}</details>}</>}</section>;
}
