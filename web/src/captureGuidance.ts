import type { Capture, Result } from './api';

/** A review route is appropriate only when appearance is the remaining uncertainty. */
export function appearanceNeedsReview(result: Result | null | undefined, capture: Capture | null | undefined) {
  if (!result || result.accepted || result.hard_error || result.outcome !== 'unknown' || capture?.slot !== 'counterpart') return false;
  const unresolved = result.checks.filter(check => !check.passed);
  return unresolved.length > 0 && unresolved.every(check => check.outcome === 'unknown' && check.name.startsWith('appearance_continuity:'));
}

export function appearanceGuidance(result: Result | null | undefined, capture: Capture | null | undefined) {
  if (!appearanceNeedsReview(result, capture)) return null;
  return {
    title: 'Both photos are saved. Appearance needs review.',
    message: 'The remaining uncertainty is how the objects look. Reflections, transparent surfaces and lighting can change the color comparison even when an object stays the same. Review these saved photos; another capture may not resolve it.',
  };
}

// Collector advice is separate from the immutable verifier result. The detailed
// checks and exported evidence retain the original decision and measurements.
export function registrationGuidance(result: Result | null | undefined, capture: Capture | null | undefined, objectA: string, objectB: string) {
  if (!result || result.accepted || result.outcome !== 'unknown' || result.code !== 'registration_uncertain') return null;
  const uncertain = result.checks.find(check => check.code === 'registration_uncertain' && check.outcome === 'unknown');
  const asset = uncertain?.measurements?.candidate_sha256;
  const frame = typeof asset === 'string' ? capture?.frames.find(item => item.asset_id === asset) : undefined;
  const personIsProtected = result.checks.some(check => check.name.startsWith('protected_object:person:'));
  const dominantPerson = objectA !== 'person' && objectB !== 'person' && !personIsProtected && frame?.detections?.some(item =>
    item.label === 'person' && item.confidence >= .35 && (item.box[2] - item.box[0]) * (item.box[3] - item.box[1]) > .5);
  const baseline = capture?.slot === 'baseline';
  return {
    title: dominantPerson ? 'Keep your body out of the camera view.' : 'Show more fixed background detail.',
    message: dominantPerson
      ? `A detected person covers most of this image, so that region cannot establish a fixed background. ${baseline ? 'Aim the camera toward the desk, set the objects down, and step out of view. Then retake the first image.' : 'Keep the camera in its saved position and step out of view before capturing again.'}`
      : baseline
        ? 'Before saving the first image, we need background details spread around the objects. Include desk edges or patterned paper; a blank wall or table may not provide enough detail.'
        : 'The background could not be matched reliably. Keep the camera at its saved angle, clear your hands from the view, and restore any covered background details. This does not establish that the camera moved.',
  };
}
