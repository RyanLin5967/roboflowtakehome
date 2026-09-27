import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import type { Mode, Recipe } from './api';
import { Alert } from './Shell';
import { humanName, jsonPost, messageOf, operator } from './productApi';
import type { Campaign } from './productApi';

const fallbackClasses = ['bottle', 'cup', 'book', 'cell phone', 'mouse'];
export default function CampaignForm({ onCreated, onClose }: { onCreated: (campaign: Campaign) => void; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden';
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current(); }
      if (event.key !== 'Tab') return;
      const items = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]') || []);
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', handleKey); previous?.focus(); };
  }, []);
  const [name, setName] = useState('');
  const [recipe, setRecipe] = useState<Recipe>('spatial');
  const [mode, setMode] = useState<Mode>('live');
  const [objectA, setObjectA] = useState('bottle');
  const [objectB, setObjectB] = useState('cup');
  const [target, setTarget] = useState(20);
  const [classes, setClasses] = useState(fallbackClasses);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { request<{ recipes: Array<{ supported_objects?: string[] }> }>('/recipes').then(data => { const supported = data.recipes[0]?.supported_objects; if (supported?.length) setClasses(supported); }).catch(() => undefined); }, []);
  return <div className="form-overlay"><section ref={dialogRef} className="campaign-form" role="dialog" aria-modal="true" aria-labelledby="collection-form-title"><div className="panel-heading"><p className="eyebrow">A DELIBERATE DATASET</p><button className="close-button" onClick={onClose} aria-label="Close collection form">×</button></div><h2 id="collection-form-title">Create a collection.</h2><p className="form-intro">Define the change once. Every collector follows the same versioned task.</p><form onSubmit={async event => { event.preventDefault(); setBusy(true); setError(null); try { onCreated(await operator<Campaign>('/campaigns', jsonPost({ name, recipe, mode, object_a: objectA, object_b: objectB, target_pairs: target }))); } catch (cause) { setError(messageOf(cause)); } finally { setBusy(false); } }}>
    <label htmlFor="collection-name">Collection name</label><input id="collection-name" autoFocus value={name} onChange={event => setName(event.target.value)} maxLength={120} placeholder="Desk objects, opposite stories" required/>
    <fieldset><legend>What changes between observations?</legend><div className="form-recipes"><button type="button" className={recipe === 'spatial' ? 'selected' : ''} onClick={() => setRecipe('spatial')} aria-pressed={recipe === 'spatial'}><span>⇄</span><strong>Switch sides</strong><small>One object moves. The reference stays still.</small></button><button type="button" className={recipe === 'temporal' ? 'selected' : ''} onClick={() => setRecipe('temporal')} aria-pressed={recipe === 'temporal'}><span>①②</span><strong>Reverse arrival order</strong><small>The same ending, a different order.</small></button></div></fieldset>
    <div className="form-two"><label>Changing object<select aria-label="Changing object" disabled={mode === 'replay'} value={objectA} onChange={event => setObjectA(event.target.value)}>{classes.map(label => <option value={label} key={label}>{humanName(label)}</option>)}</select></label><label>{recipe === 'spatial' ? 'Stationary reference' : 'Second object'}<select aria-label="Reference object" disabled={mode === 'replay'} value={objectB} onChange={event => setObjectB(event.target.value)}>{classes.map(label => <option value={label} key={label}>{humanName(label)}</option>)}</select></label></div>
    {objectA === objectB && <p className="field-error">Choose two different classes so the verifier can distinguish their roles.</p>}<p className="fine-print">One visible instance of each class. Recognition still needs validation in the actual scene.</p>
    <div className="form-two"><label>Approved pair target<input aria-label="Approved pair target" type="number" min={1} max={10000} value={target} onChange={event => setTarget(Number(event.target.value))} required/></label><label>Collection source<select aria-label="Collection source" value={mode} onChange={event => { const value = event.target.value as Mode; setMode(value); if (value === 'replay') { setObjectA('bottle'); setObjectB('cup'); } }}><option value="live">Real camera captures</option><option value="replay">Labeled test replay</option></select></label></div><Alert message={error}/><button className="primary" disabled={busy || !name.trim() || objectA === objectB}>{busy ? 'Creating…' : 'Create collection'}<span aria-hidden="true">↗</span></button>
  </form></section></div>;
}
