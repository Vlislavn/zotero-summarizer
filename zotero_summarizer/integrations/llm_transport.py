"""Request-owned destination/auth for connection metadata and declared no-auth clients."""
from urllib.parse import urlsplit
import os

import httpx

def own_headers(request, api_key, *, native=False, raw_response=False):
    """Only request-owned values; even SDK allowlisted header names can be poisoned."""
    for name in list(request.headers):
        del request.headers[name]
    request.headers.update({'host': request.url.netloc.decode('ascii'), 'accept': 'application/json',
                            'accept-encoding': 'identity', 'user-agent': 'zotero-summarizer/connection'})
    if request.content:
        request.headers.update({'content-type': 'application/json', 'content-length': str(len(request.content))})
    if raw_response:
        # SDK control, reconstructed from the sync OnPrem owner, never ambient input.
        request.headers['x-stainless-raw-response'] = 'true'
    if native:
        request.headers['anthropic-version'] = '2023-06-01'
    if api_key is not None:
        request.headers['x-api-key' if native else 'authorization'] = api_key if native else f'Bearer {api_key}'


def validate_base(base_url: str):
    url = urlsplit(base_url)
    if (url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path.lower().endswith(('.gguf', '.bin'))):
        raise ValueError('An exact HTTP(S) API base without credentials, query or fragment is required')
    return httpx.URL(base_url)


def owned_http_client(base_url, api_key, *, timeout, native=False, before_send=None, transport=None, raw_response=False):
    base = validate_base(base_url)

    def own(request):
        target = request.url
        prefix = base.raw_path.rstrip(b'/') + b'/'
        if (target.userinfo or (target.scheme, target.netloc) != (base.scheme, base.netloc)
                or not target.raw_path.startswith(prefix)):
            raise ValueError('Request differs from the approved API destination')
        own_headers(request, api_key, native=native, raw_response=raw_response)
        if before_send:
            before_send(request)

    return httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout,
                        transport=transport, event_hooks={'request': [own]})


def _omit_ambient_headers(variable, omit):
    # Match the installed SDK's custom-header parser, overriding names, never values.
    return {line.partition(':')[0].strip(): omit() for line in os.getenv(variable, '').split('\n') if ':' in line}


def native_client_options(base_url, api_key, *, http_client):
    from anthropic import Omit

    return {'api_key': api_key or '', 'auth_token': '',
            'base_url': base_url or 'https://api.anthropic.com',
            'max_retries': 0, 'http_client': http_client,
            'default_headers': {**_omit_ambient_headers('ANTHROPIC_CUSTOM_HEADERS', Omit), 'X-Api-Key': api_key, 'Authorization': Omit()}}


class SynchronousCompletionsOnly:
    """The application's LLMClient is sync-only; never create an ambient async SDK."""
    @property
    def with_raw_response(self):
        return self

    async def create(self, **kwargs):
        raise RuntimeError('This application client supports synchronous requests only')


def no_auth_onprem_options(base_url, before_send=None, *, timeout):
    from openai import Omit

    transport = owned_http_client(base_url, None, before_send=before_send, timeout=timeout, raw_response=True)
    try:
        client = openai_client(base_url, http_client=transport)
    except BaseException:
        transport.close()
        raise
    return transport, {'client': client.chat.completions, 'root_client': client,
                       'async_client': SynchronousCompletionsOnly(),
                       'openai_api_key': '', 'openai_proxy': '', 'max_retries': 0, 'cache': False,
                       'model_kwargs': {'extra_headers': {'Authorization': Omit()}}}


def openai_client(base_url, *, http_client):
    """Callable empty auth is SDK-supported, unlike a dummy key or ambient None."""
    from openai import OpenAI, Omit
    client = OpenAI(api_key=lambda: '', admin_api_key='', organization='', project='', webhook_secret='',
                    base_url=base_url, max_retries=0, http_client=http_client,
                    default_headers={**_omit_ambient_headers('OPENAI_CUSTOM_HEADERS', Omit), 'Authorization': Omit()})
    return client
