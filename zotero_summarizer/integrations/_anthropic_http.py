"""Native no-auth protocol helpers; no SDK client/credential/profile construction."""
from pydantic import TypeAdapter

NATIVE_VERSION = '2023-06-01'


def message(client, base_url, payload, *, output_format=None):
    # These are the installed SDK's pure helpers, also used by messages.parse.
    from anthropic.types import Message
    from anthropic import transform_schema
    from anthropic.lib._parse._response import parse_response

    body = dict(payload)
    if output_format is not None:
        schema = transform_schema(TypeAdapter(output_format).json_schema())
        body['output_config'] = {'format': {'type': 'json_schema', 'schema': schema}}
    response = client.post(base_url.rstrip('/') + '/v1/messages', json=body,
                           headers={'anthropic-version': NATIVE_VERSION})
    response.raise_for_status()
    parsed = Message.model_validate(response.json())
    return parse_response(response=parsed, output_format=output_format) if output_format is not None else parsed


def models(client, base_url):
    response = client.get(base_url.rstrip('/') + '/v1/models', params={'limit': 1000},
                          headers={'anthropic-version': NATIVE_VERSION})
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get('data'), list):
        raise ValueError('Malformed native model catalog')
    return [row['id'] for row in data['data'] if isinstance(row, dict) and isinstance(row.get('id'), str) and row['id']]
