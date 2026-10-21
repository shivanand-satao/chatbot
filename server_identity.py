"""Fixed-origin backend profile verification; no JWT secrets or local JWT decoding.

Configuration is supplied by the server, never from a request. No connections
are opened until verify() is called. No redirects, proxy discovery, or retries.
"""

import http.client
import json
import re
from urllib.parse import urlsplit

from conversation_context import Owner


class IdentityRejected(Exception):
    pass


class IdentityUnavailable(Exception):
    pass


def bearer_header(value):
    if (not isinstance(value, str) or len(value) > 8192
            or re.fullmatch(r'Bearer [A-Za-z0-9._~+/=-]+', value) is None):
        raise IdentityRejected('Authentication required')
    return value


def owner_from_profile(payload):
    """Only call with a successful response from the configured trusted backend."""
    if not isinstance(payload, dict) or payload.get('success') is not True:
        raise IdentityUnavailable('Identity service returned an invalid profile')
    user = payload.get('user')
    if not isinstance(user, dict):
        raise IdentityUnavailable('Identity service returned an invalid profile')
    role = user.get('role')
    roles = {'user': 'user', 'admin': 'admin', 'superadmin': 'admin', 'moderator': 'admin'}
    if not isinstance(role, str) or role not in roles:
        raise IdentityRejected('Account role is not supported')
    if user.get('account_kind') != roles[role] or user.get('status') != 'active':
        raise IdentityRejected('Verified active account namespace is required')
    account_id = user.get('id')
    # PostgreSQL bigint can arrive from Node pg as a decimal string. Reject
    # floats, booleans, signs, whitespace, and unsafe numeric JSON integers.
    if type(account_id) is int:
        valid = 1 <= account_id <= 9007199254740991
    elif isinstance(account_id, str):
        valid = re.fullmatch(r'[1-9][0-9]{0,18}', account_id) is not None
        if valid:
            account_id = int(account_id)
            valid = account_id <= 9223372036854775807
    else:
        valid = False
    if not valid:
        raise IdentityUnavailable('Identity service returned an invalid account ID')
    return Owner(roles[role], account_id)


class ProfileVerifier:
    def __init__(self, origin, timeout=5, allow_loopback_http=False):
        if not isinstance(origin, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in origin):
            raise ValueError('Expected an ASCII backend origin')
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError('Invalid backend port') from None
        local_http = (allow_loopback_http is True and parsed.scheme == 'http'
                      and parsed.hostname in ('localhost', '127.0.0.1', '::1'))
        if (parsed.scheme != 'https' and not local_http) or not parsed.hostname:
            raise ValueError('Backend origin must use HTTPS (or explicit loopback development HTTP)')
        if (parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or '\\' in origin or '%' in parsed.netloc or port == 0):
            raise ValueError('Configure an origin, not credentials, a path, or a query')
        if type(timeout) not in (int, float) or not 0 < timeout <= 10:
            raise ValueError('Expected a timeout of at most ten seconds')
        self.host, self.port = parsed.hostname, port
        self.timeout, self.secure = timeout, parsed.scheme == 'https'

    def verify(self, authorization):
        authorization = bearer_header(authorization)
        connection_type = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
        connection = connection_type(self.host, self.port, timeout=self.timeout)
        try:
            connection.request('GET', '/api/auth/profile', headers={
                'Authorization': authorization, 'Accept': 'application/json',
                'Accept-Encoding': 'identity', 'Connection': 'close'})
            response = connection.getresponse()
            if response.status in (401, 403):
                raise IdentityRejected('Authentication required')
            if response.status != 200:
                raise IdentityUnavailable('Identity service unavailable')
            if (response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json'
                    or response.getheader('Content-Encoding', 'identity').lower() != 'identity'):
                raise IdentityUnavailable('Identity service returned an invalid response')
            body = response.read(16385)
            if len(body) > 16384:
                raise IdentityUnavailable('Identity response exceeds size limit')
            try:
                payload = json.loads(body.decode('utf-8'))
            except (ValueError, UnicodeError):
                raise IdentityUnavailable('Identity service returned invalid JSON') from None
            return owner_from_profile(payload)
        except (OSError, http.client.HTTPException):
            raise IdentityUnavailable('Identity service unavailable') from None
        finally:
            connection.close()