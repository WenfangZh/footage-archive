"""The Content Security Policy of every page (ADR 0006, ADR 0007).

The browser itself refuses anything from another origin: scripts, styles, fonts, images,
workers and requests for data all come from the archive, no inline script or style runs,
and no page can be framed. That keeps the map's requests on the archive (ADR 0006), and it
is the main guard of lawyers' keys, which a script injected into a page could read.

Only lawyer pages may also run WebAssembly, which libsodium.js needs to open the vault
(ADR 0007).
"""

POLICY = '; '.join([
    "default-src 'self'",
    "img-src 'self' data: blob:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
LAWYER_POLICY = f"{POLICY}; script-src 'self' 'wasm-unsafe-eval'"


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Set once the URL is resolved; a request that matched no page has none.
        match = getattr(request, 'resolver_match', None)
        lawyer_page = match is not None and match.namespace == 'lawyer'
        response['Content-Security-Policy'] = LAWYER_POLICY if lawyer_page else POLICY
        return response
