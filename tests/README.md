# Test boundaries

`test_corpus_off_boundary.py` covers targeted refresh with corpus disabled,
initialized resources, existing/missing locks, enabled reconciliation and
unavailable/uninitialized runtime resources. OFF fixtures fail on any reader,
cache, lock or feedback I/O; these tests load no encoder or user library.

Existing engagement/reconciliation fixtures explicitly declare corpus enabled.
Their assertion trees and parametrizations are unchanged. Existing manual-edit
and pending tests retain their native-write assertions; OFF only suppresses the
subsequent optional corpus refresh, not the explicit edit.
