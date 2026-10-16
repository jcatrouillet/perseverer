import { describe, expect, it } from "vitest";

import { ApiError, AuthError } from "./api/client";
import { bulkImportErrorMessage, formatFileSize } from "./bulkImportError";

const FILE = { name: "export_12494407.zip", size: 512059263 };

describe("formatFileSize", () => {
  it("picks GB, MB or KB", () => {
    expect(formatFileSize(3.2 * 1024 ** 3)).toBe("3.2 GB");
    expect(formatFileSize(512059263)).toBe("488 MB");
    expect(formatFileSize(300)).toBe("1 KB");
  });
});

describe("bulkImportErrorMessage", () => {
  it("shows the API's own reason with the status, file name and size", () => {
    const detail = "The file is 488 MB, more than this server's 100 MB limit.";
    const error = new ApiError(413, JSON.stringify({ detail }), detail);
    expect(bulkImportErrorMessage(error, FILE)).toBe(
      `Could not upload export_12494407.zip (488 MB). ${detail} (HTTP 413)`,
    );
  });

  it("names the proxy limit when a 413 comes from a web server in front of the API", () => {
    const error = new ApiError(413, "<html>413 Request Entity Too Large</html>", null);
    const message = bulkImportErrorMessage(error, FILE);
    expect(message).toContain("HTTP 413");
    expect(message).toContain("PERSEVERER_MAX_UPLOAD_MB");
    expect(message).toContain("client_max_body_size");
  });

  it("explains a proxy timeout and a dropped connection", () => {
    expect(bulkImportErrorMessage(new ApiError(504, "", null), FILE)).toContain("timed out");
    expect(bulkImportErrorMessage(new TypeError("Failed to fetch"), FILE)).toContain(
      "connection was lost",
    );
  });

  it("asks to sign in again when the session expired", () => {
    expect(bulkImportErrorMessage(new AuthError("expired"), FILE)).toContain("Sign in again");
  });
});
