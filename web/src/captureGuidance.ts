import type { Capture, Result } from './api';

// Collector advice is separate from the immutable verifier result. The detailed
// checks and exported evidence retain the original decision and measurements.
export function registrationGuidance(result: Result | null | undefined, capture: Capture | null | undefined, objectA: string, objectB: string) {
  if (!result || result.accepted || result.outcome !== 'unknown' || result.code !== 'registration_uncertain') return null;
  const uncertain = result.checks.find(check => check.code === 'registration_uncertain' && check.outcome === 'unknown');
  if (uncertain?.measurements?.background_version) {
    return { title: 'Keep the scene clearly visible.', message: uncertain.detail };
  }
  const asset = uncertain?.measurements?.candidate_sha256;
  const frame = typeof asset === 'string' ? capture?.frames.find(item => item.asset_id === asset) : undefined;
  const personIsProtected = result.checks.some(check => check.name.startsWith('protected_object:person:'));
  const dominantPerson = objectA !== 'person' && objectB !== 'person' && !personIsProtected && frame?.detections?.some(item =>
    item.label === 'person' && item.confidence >= .35 && (item.box[2] - item.box[0]) * (item.box[3] - item.box[1]) > .5);
  const baseline = capture?.slot === 'baseline';
  return {
    title: dominantPerson ? 'Keep your body out of the camera view.' : 'This saved photo needs an updated check.',
    message: dominantPerson
      ? `A detected person covers most of this image, so that region cannot establish a fixed background. ${baseline ? 'Aim the camera toward the desk, set the objects down, and step out of view. Then retake the first image.' : 'Keep the camera in its saved position and step out of view before capturing again.'}`
      : 'This attempt used an older background check. Recheck the saved photo with the updated checks, or start a new capture. Plain backgrounds do not need added patterns.',
  };
}
