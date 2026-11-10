"""Safe probe observations from typed SDK/HTTP fields, never provider prose."""
import httpx

from zotero_summarizer.api.errors import APIError, LLMTimeoutError


def catalog_observation(error):
    cause = error.__cause__ or error
    observed = failure_observation(cause, {'invalid_events': False, 'coherent': False})
    status = observed.get('http_status')
    if status is not None:
        return {'reason_code': 'catalog_http_error', 'http_status': status,
                'detail': observed['detail'], 'recovery': 'review_authorization' if status in {401, 403} else 'review_destination'}
    codes = {'inference_timeout': ('catalog_timeout', 'Metadata request timed out. Review server availability; no model was tested.'),
             'inference_transport_error': ('catalog_transport_error', 'Metadata transport failed. Review the exact server and its availability.'),
             'missing_api_key': ('catalog_authorization_absent', 'No authorization is stored for this server. Establish authorization separately, or deliberately choose no auth.')}
    code, detail = codes.get(observed['reason_code'],
                             ('catalog_invalid_response', 'The server did not return a valid model catalog. Review its protocol/base or enter a manual ID; authorization and operation remain unverified.'))
    return {'reason_code': code, 'detail': detail, 'http_status': None,
            'recovery': 'review_authorization' if code == 'catalog_authorization_absent' else 'review_destination'}


def failure_observation(error, completion):
    # Both SDKs retain the HTTP response/status; HTTPX owns the no-auth native seam.
    status = getattr(getattr(error, 'response', None), 'status_code', None)
    if isinstance(error, APIError):
        status = error.details.get('http_status', status)
    if type(status) is int and 100 <= status <= 599:
        recovery = ('Review authorization for this exact server.' if status in {401, 403}
                    else 'Review the exact API base, protocol and model; this status alone does not identify the wrong setting.'
                    if status == 404 else 'Review the server status and request settings before deliberately testing again.')
        return {'reason_code': 'inference_http_error', 'http_status': status,
                'detail': f'Server returned HTTP {status}. {recovery} No provider error body is disclosed.'}
    # Lazy like the adapters: only an actual probe failure needs SDK exception types.
    import anthropic
    import openai

    if isinstance(error, (TimeoutError, LLMTimeoutError, httpx.TimeoutException,
                          anthropic.APITimeoutError, openai.APITimeoutError)):
        return {'reason_code': 'inference_timeout',
                'detail': 'The model request timed out. Remote completion is not established; inspect ownership before retrying.'}
    if isinstance(error, (httpx.TransportError, ConnectionError, anthropic.APIConnectionError, openai.APIConnectionError)):
        return {'reason_code': 'inference_transport_error',
                'detail': 'The model transport failed. Review server availability and the exact destination; remote completion is not established.'}
    if completion['invalid_events']:
        return {'reason_code': 'request_evidence_invalid',
                'detail': 'Native model identity or terminal/ownership evidence did not match this request. Review the saved model and protocol.'}
    if completion['coherent'] and not completion['successful']:
        return {'reason_code': 'request_output_truncated',
                'detail': 'The server reported an exhausted output budget, not a successful completion. Review model output settings.'}
    if isinstance(error, APIError) and error.error in {'inference_empty_response', 'request_inventory_incomplete'}:
        return {'reason_code': error.error, 'detail': error.message}
    if isinstance(error, APIError) and error.error == 'missing_api_key':
        return {'reason_code': error.error, 'detail': 'No authorization is available for this saved server. Review its authorization or deliberately choose no auth.'}
    return {'reason_code': 'inference_failed',
            'detail': 'The response could not establish a valid model completion. Review the exact server, protocol and model; no provider error text is disclosed.'}
