import type { Check } from './api';
import { humanName } from './productApi';

export const checkOutcome = (check: Check) => check.outcome || (check.passed ? 'pass' : 'fail');
export const outcomeName = (outcome: 'pass' | 'fail' | 'unknown') => ({ pass: 'Pass', fail: 'Fail', unknown: 'Unknown' })[outcome];
export const outcomeSymbol = (outcome: 'pass' | 'fail' | 'unknown') => ({ pass: '✓', fail: '!', unknown: '?' })[outcome];
export default function RecordedChecks({ checks }: { checks: Check[] }) {
  return <ul className="checks">{checks.map((check, index) => { const outcome = checkOutcome(check); return <li className={outcome} key={`${check.name}-${index}`}><span aria-label={outcomeName(outcome)}>{outcomeSymbol(outcome)}</span><span>{humanName(check.name)} <em className="check-outcome">{outcomeName(outcome)}</em>{check.detail && <small>{check.detail}</small>}</span></li>; })}</ul>;
}
