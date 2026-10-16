// What the Settings bulk import (components/BulkImportCard.tsx) says when an upload fails: the
// file, its size, the HTTP status and the server's own reason, so a failure can be diagnosed
// from a screenshot. The API explains every rejection itself (too large, not a zip, no disk
// space...); a status with no API detail came from a proxy in front of it, which the hint names.
import { ApiError, AuthError, ServerUnconfiguredError } from "./api/client";

export function formatFileSize(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function proxyHint(status: number): string {
  switch (status) {
    case 413:
      return (
        "The file is larger than a web server in front of Perseverer accepts. Raise " +
        "PERSEVERER_MAX_UPLOAD_MB, and the request body limit of any reverse proxy in front " +
        "of the app (nginx: client_max_body_size)."
      );
    case 502:
    case 503:
      return "The API could not be reached. Check that the perseverer-api container is running.";
    case 504:
      return (
        "A proxy timed out during the upload. Raise the reverse proxy's timeouts, or use " +
        "`sync import` on the server for very large exports."
      );
    default:
      return "The response did not come from the Perseverer API; check the reverse proxy logs.";
  }
}

export function bulkImportErrorMessage(
  error: unknown,
  file: { name: string; size: number },
): string {
  const what = `Could not upload ${file.name} (${formatFileSize(file.size)})`;
  if (error instanceof AuthError) return `${what}: your session expired. Sign in again and retry.`;
  if (error instanceof ApiError) {
    return error.detail
      ? `${what}. ${error.detail} (HTTP ${error.status})`
      : `${what}: HTTP ${error.status}. ${proxyHint(error.status)}`;
  }
  if (error instanceof ServerUnconfiguredError) return `${what}: HTTP 503. ${proxyHint(503)}`;
  if (error instanceof TypeError) {
    return (
      `${what}: the connection was lost before the server answered (network drop, or a proxy ` +
      "closing a long upload). Check your connection and the reverse proxy's limits, then retry."
    );
  }
  return `${what}: ${error instanceof Error ? error.message : String(error)}`;
}
