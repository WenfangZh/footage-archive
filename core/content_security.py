"""The Content Security Policy of every page (ADR 0006, ADR 0007).

The browser itself refuses anything from another origin: scripts, styles, fonts, images,
workers and requests for data all come from the archive, no inline script or style runs,
and no page can be framed. That keeps the map's requests on the archive (ADR 0006), and it
is the main guard of lawyers' keys, which a script injected into a page could read.

Only lawyer pages, and the pages where an applicant or lawyer sets up, unlocks, recovers or
replaces their key, may also run WebAssembly, which libsodium.js needs (ADR 0007).
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
KEY_PAGES = {'accounts:key', 'accounts:key_setup', 'accounts:key_recover', 'accounts:key_exposure'}


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Set once the URL is resolved; a request that matched no page has none.
        match = getattr(request, 'resolver_match', None)
        # The outermost namespace, so that pages in a namespace nested under it count too.
        lawyer_page = match is not None and (match.namespaces[:1] == ['lawyer'] or match.view_name in KEY_PAGES)
        response['Content-Security-Policy'] = LAWYER_POLICY if lawyer_page else POLICY
        return response
