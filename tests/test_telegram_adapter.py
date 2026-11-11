"""Offline actual TL components with an awaitable fake, never a real SDK client."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("telethon")
from telethon import helpers, utils
from telethon.errors import rpcbaseerrors, rpcerrorlist
from telethon.tl import functions, types

from zotero_summarizer.integrations.telegram import TelethonPageClient
from zotero_summarizer.models.research_feed import TelegramTransportError


NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
A = -1000000000001
INT32_MAX = 2**31 - 1


def channel(native=1, **changes):
    values = dict(id=native, title="fixture", photo=types.ChatPhotoEmpty(), date=NOW,
                  username="alpha", broadcast=True, access_hash=7)
    return types.Channel(**(values | changes))


def resolved(value=None):
    value = channel() if value is None else value
    return types.contacts.ResolvedPeer(types.PeerChannel(value.id), [value], [])


def message(number=7, native=1, **changes):
    values = dict(id=number, peer_id=types.PeerChannel(native), date=NOW,
                  message="  🧪 Подпись / caption\n原文  ")
    return types.Message(**(values | changes))


def history(posts, family=types.messages.Messages):
    values = dict(messages=posts, topics=[], chats=[], users=[])
    if family is not types.messages.Messages:
        values["count"] = 999
    if family is types.messages.ChannelMessages:
        values["pts"] = 1
    return family(**values)


class ScriptSDK:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def __call__(self, request):
        self.calls.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def __getattr__(self, name):
        raise AssertionError("leaf used a convenience/lifecycle operation")


def fetch(posts, *, after=None, family=types.messages.Messages):
    sdk = ScriptSDK(resolved(), history(posts, family))

    async def run():
        leaf = TelethonPageClient(sdk)
        await leaf.resolve_channel(username="alpha")
        return await leaf.fetch_page(channel_id=A, after_id=after, limit=100)

    return asyncio.run(run()), sdk


@pytest.mark.parametrize("after,offset,extra", [(None, 0, 0), (0, 1, -100),
                                               (7, 8, -100), (INT32_MAX - 1, INT32_MAX, -100)])
@pytest.mark.parametrize("access_hash", [-(2**63), 0, 2**63 - 1])
def test_request_parameters_and_actual_serialization(after, offset, extra, access_hash):
    sdk = ScriptSDK(resolved(channel(access_hash=access_hash)), history([]))

    async def run():
        leaf = TelethonPageClient(sdk)
        assert sdk.calls == []
        identity = await leaf.resolve_channel(username="alpha")
        page = await leaf.fetch_page(channel_id=identity.channel_id, after_id=after, limit=100)
        assert page.complete and page.posts == ()
        request = sdk.calls[1]
        assert type(sdk.calls[0]) is functions.contacts.ResolveUsernameRequest
        assert sdk.calls[0].username == "alpha"
        assert type(request) is functions.messages.GetHistoryRequest
        assert type(request.peer) is types.InputPeerChannel
        assert (request.peer.channel_id, request.peer.access_hash) == (1, access_hash)
        assert (request.offset_id, request.add_offset, request.limit) == (offset, extra, 100)
        assert (request.offset_date, request.min_id, request.max_id, request.hash) == (None, 0, 0, 0)
        assert bytes(request) and bytes(request.peer)
        class LocalInput:
            async def get_input_entity(self, peer):
                return utils.get_input_peer(peer)
        original = request.peer
        await request.resolve(LocalInput(), utils)
        assert request.peer is original
        assert len(sdk.calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("field,value", [("after_id", True), ("after_id", -1),
    ("after_id", INT32_MAX), ("after_id", 2**63 - 1), ("after_id", 1.5),
    ("limit", True), ("limit", 100.0), ("limit", 99), ("channel_id", 1),
    ("channel_id", True), ("channel_id", -1000000000000)])
def test_invalid_fetch_does_not_consume_handle_or_call_sdk(field, value):
    sdk = ScriptSDK(resolved(), history([]))

    async def run():
        leaf = TelethonPageClient(sdk)
        await leaf.resolve_channel(username="alpha")
        arguments = dict(channel_id=A, after_id=None, limit=100) | {field: value}
        with pytest.raises(ValueError):
            await leaf.fetch_page(**arguments)
        assert len(sdk.calls) == 1
        await leaf.fetch_page(channel_id=A, after_id=None, limit=100)
        with pytest.raises(ValueError):
            await leaf.fetch_page(channel_id=A, after_id=None, limit=100)
        assert len(sdk.calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("key", ["@alpha", "Alpha", " alpha ", "", "me/other", False])
def test_literal_canonical_resolution_before_io(key):
    sdk = ScriptSDK()
    with pytest.raises(ValueError):
        asyncio.run(TelethonPageClient(sdk).resolve_channel(username=key))
    assert sdk.calls == []


@pytest.mark.parametrize("kind", ["primary", "active_alias"])
def test_fresh_public_names_mark_once_and_use_verified_alias(kind, monkeypatch):
    value = channel(username="ALPHA") if kind == "primary" else channel(
        username="other", usernames=[types.Username("ALPHA", active=True)])
    sdk = ScriptSDK(resolved(value), history([message()]))
    real_mark = utils.get_peer_id
    calls = []

    def marked(peer):
        assert type(peer) is types.PeerChannel
        calls.append(peer.channel_id)
        return real_mark(peer)

    monkeypatch.setattr(utils, "get_peer_id", marked)

    async def run():
        leaf = TelethonPageClient(sdk)
        assert (await leaf.resolve_channel(username="alpha")).channel_id == A
        value.access_hash = 900
        page = await leaf.fetch_page(channel_id=A, after_id=None, limit=100)
        assert sdk.calls[-1].peer.access_hash == 7
        assert page.posts[0].username == "alpha"
        assert page.posts[0].permalink == "https://t.me/alpha/7"

    asyncio.run(run())
    assert calls == [1]


@pytest.mark.parametrize("changes", [{"broadcast": False}, {"megagroup": True},
    {"gigagroup": True}, {"min": True}, {"access_hash": None}, {"username": None},
    {"username": "other", "usernames": [types.Username("alpha", active=False)]}])
def test_legitimate_ineligible_channels(changes):
    with pytest.raises(TelegramTransportError) as caught:
        asyncio.run(TelethonPageClient(ScriptSDK(resolved(channel(**changes)))).resolve_channel(username="alpha"))
    assert caught.value.code == "channel_unavailable"


@pytest.mark.parametrize("changes", [{"id": True}, {"id": 0}, {"id": 2**63 - 1},
    {"access_hash": False}, {"access_hash": 2**63}, {"access_hash": -(2**63) - 1},
    {"broadcast": 1}, {"megagroup": 0}, {"min": "private"}, {"username": False},
    {"usernames": False}, {"usernames": [False]},
    {"usernames": [types.Username("alpha", active=1)]}])
def test_malformed_channel_values_are_loud(changes):
    with pytest.raises(ValueError):
        asyncio.run(TelethonPageClient(ScriptSDK(resolved(channel(**changes)))).resolve_channel(username="alpha"))


@pytest.mark.parametrize("kind", ["wrong_target", "duplicate", "wrong_shape", "bad_peer"])
def test_malformed_resolution_no_first_row_rescue(kind):
    result = resolved()
    if kind == "wrong_target":
        result.chats = [channel(2)]
    elif kind == "duplicate":
        result.chats *= 2
    elif kind == "bad_peer":
        result.peer = False
    else:
        result = {"peer": types.PeerChannel(1)}
    with pytest.raises(ValueError):
        asyncio.run(TelethonPageClient(ScriptSDK(result)).resolve_channel(username="alpha"))


@pytest.mark.parametrize("peer", [types.PeerUser(1), types.PeerChat(1)])
def test_nonchannel_target_is_unavailable(peer):
    result = types.contacts.ResolvedPeer(peer, [], [])
    with pytest.raises(TelegramTransportError) as caught:
        asyncio.run(TelethonPageClient(ScriptSDK(result)).resolve_channel(username="alpha"))
    assert caught.value.code == "channel_unavailable"


def test_forbidden_and_failed_resolution_clear_previous_handle():
    forbidden = types.ChannelForbidden(1, 7, "fixture", broadcast=True)
    sdk = ScriptSDK(resolved(), resolved(forbidden))

    async def run():
        leaf = TelethonPageClient(sdk)
        await leaf.resolve_channel(username="alpha")
        with pytest.raises(TelegramTransportError):
            await leaf.resolve_channel(username="alpha")
        with pytest.raises(ValueError):
            await leaf.fetch_page(channel_id=A, after_id=None, limit=100)
        assert len(sdk.calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("family", [types.messages.Messages, types.messages.MessagesSlice,
                                   types.messages.ChannelMessages])
@pytest.mark.parametrize("size", [0, 1, 100])
def test_whole_response_family_sparse_order_and_no_refill(family, size):
    posts = [message(i * 3, date=NOW - timedelta(days=i)) for i in range(size, 0, -1)]
    page, sdk = fetch(posts, family=family)
    assert page.complete and len(page.posts) == size
    assert [p.message_id for p in page.posts] == [i * 3 for i in range(1, size + 1)]
    assert len(sdk.calls) == 2


@pytest.mark.parametrize("kind", ["oversize", "duplicate", "ascending", "wrong_peer", "bad_id",
    "overflow", "unknown", "notmodified", "bad_list", "old_cursor", "bad_peer_scalar"])
def test_malformed_pages_fail_not_incomplete(kind):
    posts = [message(9), message(7)]
    response = history(posts)
    if kind == "oversize":
        response.messages = [message(i) for i in range(101, 0, -1)]
    elif kind == "duplicate":
        posts[1].id = 9
    elif kind == "ascending":
        posts.reverse()
    elif kind == "wrong_peer":
        posts[1].peer_id = types.PeerChannel(2)
    elif kind == "bad_id":
        posts[1].id = True
    elif kind == "overflow":
        posts[0].id = INT32_MAX + 1
    elif kind == "unknown":
        posts[1] = False
    elif kind == "notmodified":
        response = types.messages.MessagesNotModified(0)
    elif kind == "bad_list":
        response.messages = False
    elif kind == "bad_peer_scalar":
        posts[1].peer_id.channel_id = True
    sdk = ScriptSDK(resolved(), response)

    async def run():
        leaf = TelethonPageClient(sdk)
        await leaf.resolve_channel(username="alpha")
        with pytest.raises(ValueError):
            await leaf.fetch_page(channel_id=A, after_id=7 if kind == "old_cursor" else None, limit=100)

    asyncio.run(run())


def test_exact_caption_utf16_urls_order_duplicates_and_utc_edit():
    raw = "🧪 https://example.test/β label 🧬\n"
    offset = len(helpers.add_surrogate("🧪 "))
    url = "https://example.test/β"
    entities = [types.MessageEntityTextUrl(offset, len(url), "https://hidden.test/🧬"),
                types.MessageEntityBold(0, 2), types.MessageEntityUrl(offset, len(url)),
                types.MessageEntityUrl(offset, len(url))]
    edited = NOW.astimezone(timezone(timedelta(hours=3)))
    page, _ = fetch([message(message=raw, entities=entities, edit_date=edited, pinned=True)])
    post = page.posts[0]
    assert post.text == raw and post.skip_reason is None
    assert post.urls == ["https://hidden.test/🧬", url, url]
    assert post.published_at == post.updated_at == NOW.isoformat()


@pytest.mark.parametrize("kind,reason", [("service", "service_message"), ("media", "media_only"),
    ("empty", "empty_message"), ("rich", "unsupported_original")])
def test_dated_skip_observations(kind, reason):
    post = message(message="  ")
    if kind == "service":
        post = types.MessageService(id=7, peer_id=types.PeerChannel(1), date=NOW, action=types.MessageActionEmpty())
    elif kind == "media":
        post.media = types.MessageMediaEmpty()
    elif kind == "rich":
        post.rich_message = types.RichMessage([], [], [])
    page, _ = fetch([post])
    assert page.complete and page.posts[0].skip_reason == reason
    assert page.posts[0].text == ("" if kind == "service" else "  ")


@pytest.mark.parametrize("kind", ["message_empty", "missing_date", "service_date", "bare_url", "other_scheme"])
def test_known_unrepresentable_is_empty_incomplete_never_partial(kind):
    bad = message(7)
    if kind == "message_empty":
        bad = types.MessageEmpty(7, None)
    elif kind == "missing_date":
        bad.date = None
    elif kind == "service_date":
        bad = types.MessageService(id=7, peer_id=types.PeerChannel(1), date=None, action=types.MessageActionEmpty())
    else:
        bad.entities = [types.MessageEntityTextUrl(0, 1, "bare.test" if kind == "bare_url" else "tg://user?id=1")]
    page, _ = fetch([message(9), bad])
    assert not page.complete and page.posts == ()


@pytest.mark.parametrize("field,value", [("message", False), ("message", 0), ("date", False),
    ("date", datetime(2026, 10, 8)), ("edit_date", "private"), ("entities", False),
    ("entities", [False]), ("media", False), ("rich_message", False)])
def test_original_bad_types_stay_loud_even_after_unrepresentable(field, value):
    bad = message(7)
    setattr(bad, field, value)
    with pytest.raises(ValueError):
        fetch([types.MessageEmpty(9, None), bad])


@pytest.mark.parametrize("offset,length,target", [(True, 1, "https://a.test"), (0, False, "https://a.test"),
    (-1, 1, "https://a.test"), (0, 1000, "https://a.test"), (1, 1, "https://a.test"),
    (0, 1, "https://a.test"), (0, 2, False), (0, 2, 0)])
def test_invalid_entity_scalar_bounds_or_split_surrogate_is_loud(offset, length, target):
    entity = types.MessageEntityTextUrl(offset, length, target)
    with pytest.raises(ValueError):
        fetch([message(message="🧪 label", entities=[entity])])


ERRORS = [(rpcerrorlist.SessionRevokedError, "revoked_session"),
    (rpcerrorlist.AuthKeyUnregisteredError, "session_unauthorized"),
    (rpcerrorlist.AuthKeyDuplicatedError, "session_unauthorized"),
    (rpcerrorlist.SessionExpiredError, "session_unauthorized"),
    (rpcerrorlist.UserDeactivatedError, "session_unauthorized"),
    (rpcerrorlist.UserDeactivatedBanError, "session_unauthorized"),
    (rpcerrorlist.UsernameInvalidError, "channel_unavailable"),
    (rpcerrorlist.UsernameNotOccupiedError, "channel_unavailable"),
    (rpcerrorlist.ChannelPrivateError, "channel_unavailable"),
    (rpcerrorlist.ChannelPublicGroupNaError, "channel_unavailable")]


@pytest.mark.parametrize("phase", ["resolve", "fetch"])
@pytest.mark.parametrize("error_type,code", ERRORS + [(ConnectionError, "network_error"),
    (TimeoutError, "network_error"), (asyncio.TimeoutError, "network_error")])
def test_narrow_typed_errors_and_fetch_failure_consumes_handle(phase, error_type, code):
    error = error_type(functions.contacts.ResolveUsernameRequest("private_fixture"))
    error.args = ("private_fixture",)
    sdk = ScriptSDK(error) if phase == "resolve" else ScriptSDK(resolved(), error)

    async def run():
        leaf = TelethonPageClient(sdk)
        if phase == "fetch":
            await leaf.resolve_channel(username="alpha")
        with pytest.raises(TelegramTransportError) as caught:
            if phase == "resolve":
                await leaf.resolve_channel(username="alpha")
            else:
                await leaf.fetch_page(channel_id=A, after_id=None, limit=100)
        assert caught.value.code == code and str(caught.value) == code
        with pytest.raises(ValueError):
            await leaf.fetch_page(channel_id=A, after_id=None, limit=100)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["resolve", "fetch"])
@pytest.mark.parametrize("seconds", [10, True, 0, -1, 1.5, None])
def test_actual_floodwait_current_fields(phase, seconds):
    error = rpcerrorlist.FloodWaitError(functions.contacts.ResolveUsernameRequest("fixture"), capture=10)
    error.seconds = seconds
    expected = TelegramTransportError if type(seconds) is int and seconds > 0 else ValueError
    sdk = ScriptSDK(error) if phase == "resolve" else ScriptSDK(resolved(), error)

    async def run():
        leaf = TelethonPageClient(sdk)
        if phase == "fetch":
            await leaf.resolve_channel(username="alpha")
        with pytest.raises(expected) as caught:
            if phase == "resolve":
                await leaf.resolve_channel(username="alpha")
            else:
                await leaf.fetch_page(channel_id=A, after_id=None, limit=100)
        if expected is TelegramTransportError:
            assert caught.value.retry_seconds == 10 and caught.value.code == "flood_wait"

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["channel_invalid", "rpc", "cancel", "programming", "filesystem"])
def test_unmapped_cancellation_and_programming_are_not_diagnostics(kind):
    request = functions.contacts.ResolveUsernameRequest("private_fixture")
    error = {"channel_invalid": rpcerrorlist.ChannelInvalidError(request),
             "rpc": rpcbaseerrors.RPCError(request, "private_fixture", code=500),
             "cancel": asyncio.CancelledError(), "programming": TypeError("fixture failure"),
             "filesystem": PermissionError("fixture failure")}[kind]
    expected = {"cancel": asyncio.CancelledError, "programming": TypeError,
                "filesystem": PermissionError}.get(kind, RuntimeError)
    with pytest.raises(expected) as caught:
        asyncio.run(TelethonPageClient(ScriptSDK(error)).resolve_channel(username="alpha"))
    if expected is RuntimeError:
        assert str(caught.value) == "unmapped Telegram RPC failure"
        assert "private_fixture" not in str(caught.value)


@pytest.mark.parametrize("native,marked", [(1, A), (2**63 - 1000000000000, -(2**63))])
def test_wire_message_and_channel_domain_extrema(native, marked):
    post = message(INT32_MAX, native)
    assert bytes(post)
    sdk = ScriptSDK(resolved(channel(native)), history([post]))

    async def run():
        leaf = TelethonPageClient(sdk)
        assert (await leaf.resolve_channel(username="alpha")).channel_id == marked
        page = await leaf.fetch_page(channel_id=marked, after_id=INT32_MAX - 1, limit=100)
        assert page.posts[0].message_id == INT32_MAX
        assert bytes(sdk.calls[-1])

    asyncio.run(run())


def test_visible_url_with_whole_nonbmp_codepoint_and_generic_unauthorized():
    target = "https://example.test/🧬"
    raw = "🧪 " + target
    entity = types.MessageEntityUrl(3, len(helpers.add_surrogate(target)))
    page, _ = fetch([message(message=raw, entities=[entity])])
    assert page.posts[0].text == raw and page.posts[0].urls == [target]
    error = rpcbaseerrors.UnauthorizedError(functions.contacts.ResolveUsernameRequest("fixture"), "private_fixture")
    with pytest.raises(TelegramTransportError) as caught:
        asyncio.run(TelethonPageClient(ScriptSDK(error)).resolve_channel(username="alpha"))
    assert caught.value.code == "session_unauthorized"


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("native,access_hash", [(1, 0), (23, 7)])
@pytest.mark.parametrize("mode", ["native", "hash", "replace_native", "replace_hash",
    "bool_native", "bool_hash", "wrong_family", "same", "replace_same", "restore",
    "restored_route_wrong_body"])
def test_fetch_route_authority_is_scalar_across_await(empty, native, access_hash, mode):
    calls = []

    async def sdk(request):
        calls.append(request)
        if isinstance(request, functions.contacts.ResolveUsernameRequest):
            return resolved(channel(native, access_hash=access_hash))
        assert type(request) is functions.messages.GetHistoryRequest
        returned_native = native
        if mode == "native":
            request.peer.channel_id = returned_native = native + 1
        elif mode == "hash":
            request.peer.access_hash = access_hash + 1
        elif mode == "replace_native":
            request.peer = types.InputPeerChannel(native + 1, access_hash)
            returned_native = native + 1
        elif mode == "replace_hash":
            request.peer = types.InputPeerChannel(native, access_hash + 1)
        elif mode == "bool_native":
            request.peer.channel_id = True
        elif mode == "bool_hash":
            request.peer.access_hash = False
        elif mode == "wrong_family":
            request.peer = types.PeerChannel(native)
        elif mode == "replace_same":
            request.peer = types.InputPeerChannel(native, access_hash)
        elif mode == "restore":
            request.peer.channel_id = native + 1
            request.peer.access_hash = access_hash + 1
            request.peer.channel_id = native
            request.peer.access_hash = access_hash
        elif mode == "restored_route_wrong_body":
            request.peer.channel_id = returned_native = native + 1
            request.peer = types.InputPeerChannel(native, access_hash)
        return history([] if empty else [message(native=returned_native)])

    async def run():
        leaf = TelethonPageClient(sdk)
        identity = await leaf.resolve_channel(username="alpha")
        valid = mode in {"same", "replace_same", "restore"} or (empty and mode == "restored_route_wrong_body")
        if valid:
            page = await leaf.fetch_page(channel_id=identity.channel_id, after_id=None, limit=100)
            assert page.complete and len(page.posts) == int(not empty)
            if not empty:
                assert page.posts[0].channel_id == identity.channel_id
        else:
            with pytest.raises(ValueError):
                await leaf.fetch_page(channel_id=identity.channel_id, after_id=None, limit=100)
        with pytest.raises(ValueError):
            await leaf.fetch_page(channel_id=identity.channel_id, after_id=None, limit=100)
        assert len(calls) == 2

    asyncio.run(run())
