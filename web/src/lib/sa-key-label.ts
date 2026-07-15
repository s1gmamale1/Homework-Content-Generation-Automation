/**
 * Human-facing label for a stored SA key.
 *
 * The owning Google account is NOT present in the key JSON — it only carries
 * the robot `client_email` (veo-gemini-sa@<project>...) and `project_id`. The
 * owner's Gmail arrives solely through the uploaded filename, by convention
 * "<gmail>.json". So we surface the filename-derived Gmail as the label and
 * fall back to the project id when the filename isn't an email (e.g. the
 * default "key.json" upload name).
 */
export function keyLabel(originalFilename: string, projectId: string): string {
  const stripped = originalFilename.replace(/\.json$/i, "").trim();
  return stripped.includes("@") ? stripped : projectId;
}
