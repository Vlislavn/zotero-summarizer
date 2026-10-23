"""Opt-in bounded search-source receipts, bound inside each federation worker."""
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT = ContextVar('search_source_outcome', default=None)


@contextmanager
def capture_source_outcome(provider):
    receipt = {'provider': provider, 'status': 'unknown', 'count': None, 'error_code': None}
    token = _CURRENT.set(receipt)
    try:
        yield receipt
    finally:
        _CURRENT.reset(token)


def record_source_outcome(provider, status, *, count=None, error_code=None):
    """No queries, exception strings, URLs, credentials or content enter receipts."""
    receipt = _CURRENT.get()
    if receipt is not None and receipt['provider'] == provider:
        if status not in {'unknown', 'not_attempted', 'failed', 'success_empty', 'success_nonempty'}:
            raise ValueError('Invalid source outcome state')
        if error_code not in {None, 'http_error', 'rate_limited', 'transport_error', 'invalid_response', 'disabled'}:
            raise ValueError('Invalid source outcome code')
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError('Invalid source observation count')
        receipt.update(status=status, count=count, error_code=error_code)
