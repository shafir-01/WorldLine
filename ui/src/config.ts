// Single source for external endpoints the viewer links to or calls, and for shared story labels.
export const VERIFIER_URL = "https://worldline-verifier-301330323690.europe-north1.run.app";
export const VERIFY_ENDPOINT = `${VERIFIER_URL}/api/verify`;
export const AUTHORIZE_ENDPOINT = `${VERIFIER_URL}/api/authorize`;
export const HEALTH_ENDPOINT = `${VERIFIER_URL}/api/healthz`;
export const VERIFIER_DOCS_URL = `${VERIFIER_URL}/api/docs`;
export const RELEASE_URL = "https://gitlab.com/shafir2/WorldLine/-/releases/v0.1.3-transcend";
export const RELEASE_TAG = "v0.1.3-transcend";
export const DEFAULT_POLICY = "release";
/** The verifier refuses bodies above this size (service/app.py MAX_BODY_BYTES). */
export const MAX_BUNDLE_BYTES = 5 * 1024 * 1024;
export const FOOTER_TEXT = "All scenarios synthetic. Transfer is within the shared Event v1 substrate, not across heterogeneous codebases.";

/** The seven story beats: full title (headline) and the short label used by the progress bar. */
export const STORY_BEATS: { title: string; short: string }[] = [
  { title: "One hour, replayed many ways", short: "Replay" },
  { title: "The replay that broke", short: "The break" },
  { title: "Narrowed to one cause", short: "One cause" },
  { title: "A pattern, not a one-off bug", short: "Pattern" },
  { title: "Tested on a different system", short: "Elsewhere" },
  { title: "The release waits for signed evidence", short: "Evidence" },
  { title: "Verify a release yourself", short: "Verify" },
];
