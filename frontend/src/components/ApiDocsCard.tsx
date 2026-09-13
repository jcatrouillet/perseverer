// Settings page: a link to the REST API reference (frontend/public/api-docs.html, a polished
// HTML twin of docs/API.md served statically by this same container) -- previously only reachable
// by knowing the /api-docs.html path directly, with nothing in the app itself pointing to it. Sits
// right after ApiKeyCard since the two are used together: generate a key, then look up how to call
// the endpoints it authenticates.
export function ApiDocsCard() {
  return (
    <section className="card">
      <h2>API documentation</h2>
      <p className="chart-note">
        Every endpoint, parameter, and response shape, for scripts, the MCP server, or anything
        else calling this app&apos;s REST API directly.
      </p>
      <a className="button" href="/api-docs.html" target="_blank" rel="noopener noreferrer">
        Open API documentation
      </a>
    </section>
  );
}
