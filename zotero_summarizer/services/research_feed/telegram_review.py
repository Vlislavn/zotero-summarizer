"""Local-only Telegram route resolution and validation of retained v2 topic bundles."""
from __future__ import annotations

from copy import deepcopy

from zotero_summarizer.models.providers import ProviderType, ResolvedStage
from zotero_summarizer.models.telegram_discussion import TelegramTopics, TelegramConversation, source_records
from zotero_summarizer.services._adapters import require_loopback_endpoint


def resolve_local(config, profile, *, allow_remote=False):
    selection = profile.telegram_model
    if not config.llm_enabled or selection is None:
        raise ValueError("explicit enabled local Telegram provider/model selection required")
    try:
        provider = config.llm_routing.provider_by_name(selection.provider)
    except KeyError:
        raise ValueError("selected local Telegram provider is unavailable") from None
    if provider.type != ProviderType.openai:
        raise ValueError("local Telegram review requires an OpenAI-compatible loopback API")
    if not allow_remote:  # remote only through telegram_processing's consent token
        require_loopback_endpoint(provider.base_url or "")
    limits = profile.telegram_limits
    # Preserve the declared API dialect, never manufacture a vLLM dialect.
    allowed = {"chat_template_kwargs", "reasoning_effort", "max_tokens", "max_completion_tokens"}
    extra = deepcopy({key: value for key, value in (provider.extra_body or {}).items() if key in allowed})
    for key in ("max_tokens", "max_completion_tokens"):
        if key in extra:
            extra[key] = limits.output_tokens
    provider = provider.model_copy(deep=True, update={
        "num_ctx": limits.context_tokens, "max_tokens": limits.output_tokens,
        "keep_alive": 0, "temperature": 0, "thinking_effort": "off",
        "extra_body": extra or None,
    })
    return ResolvedStage(stage="telegram", provider=provider, model=selection.model)


def admitted_sources(conversations):
    return {key: value for key, value in source_records(conversations).items()
            if value["original"]["text"].strip() and not value["original"].get("skip_reason")}


def validate_topics(value, conversations: list[TelegramConversation]) -> TelegramTopics:
    result = TelegramTopics.model_validate(value)
    sources = admitted_sources(conversations)
    if sources and not result.topics:
        raise ValueError("nonempty source context requires topics or an explicit processing failure")
    for topic in result.topics:
        membership = set(topic.source_ids)
        if not membership or not membership <= sources.keys():
            raise ValueError("topic cites an unknown source")
        categories = {"context": [topic.context], **{name: getattr(topic, name) for name in (
            "news", "discussion", "contrasts", "evidence", "caveats", "implications")}}
        for category, assertions in categories.items():
            for assertion in assertions:
                for citation in assertion.citations:
                    if citation.source_id not in membership:
                        raise ValueError("citation outside topic input membership")
                    source = sources[citation.source_id]
                    if not citation.quote.strip() or citation.quote not in source["original"]["text"]:
                        raise ValueError("citation quote differs from original")
                    if category in {"news", "discussion"} and source["role"] != category:
                        raise ValueError("news and discussion citations cannot be interchanged")
    return result
