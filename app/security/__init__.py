"""Security middleware.

Only the response-headers middleware lives here now. The package used to
re-export an API-key authenticator, a secrets scanner, an input validator,
request signing, secret rotation, WebSocket auth, session binding, MFA, a
zero-trust engine and a secure session store — none of which anything
imported, and several of which were second implementations of things the
app really does use: `app/tenancy/auth.py` authenticates API keys, and
`app/api/main.py` authenticates WebSocket connections inline.
"""

from app.security.headers import SecurityHeadersMiddleware

__all__ = ["SecurityHeadersMiddleware"]
