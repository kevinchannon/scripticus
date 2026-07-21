"""Wire model for the index service's token-verification endpoint (D40).

`GET /whoami` passes the caller's Gitea token straight through to Gitea
(D24's live-check pattern, as publish does — D32) and returns the token
owner's identity. The client uses it to verify a token at `login` time
instead of waiting for the first publish to fail (D34). Only the response
shape is contract; the token travels in the ``Authorization`` header.
"""

from pydantic import BaseModel


class WhoAmI(BaseModel):
    """Response for a successful token verification: the Gitea login the
    token authenticates as. Nothing ACL-shaped is returned or stored —
    permission truth stays live in Gitea (D24).
    """

    username: str
