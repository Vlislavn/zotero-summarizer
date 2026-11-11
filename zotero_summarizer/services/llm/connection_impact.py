"""Pure effective routing/dependency projection; Telegram has a separate strict route."""
from zotero_summarizer.models.providers import STAGES, resolve_stage
from zotero_summarizer.services.research_feed.telegram_review import resolve_local


def pair(resolved):
    return {'provider': resolved.provider.name, 'model': resolved.model,
            'base_url': resolved.provider.base_url, 'type': resolved.provider.type.value}


def dependencies(config, profile, provider):
    routing = config.llm_routing
    shared = [p for p in routing.providers if p.auth_mode != 'none' and p.api_key_env == provider.api_key_env]
    names = {p.name for p in shared}
    consumers = [stage for stage in STAGES if resolve_stage(routing, stage).provider.name in names]
    if routing.default.provider in names:
        consumers.insert(0, 'default')
    telegram = profile.telegram_model
    if telegram and telegram.provider in names:
        consumers.append('telegram')
    return {'providers': [p.name for p in shared], 'servers': [p.base_url or 'Native Anthropic' for p in shared], 'consumers': consumers}


def provider_consumers(config, profile, name):
    routing = config.llm_routing
    consumers = [stage for stage in STAGES if resolve_stage(routing, stage).provider.name == name]
    if routing.default.provider == name:
        consumers.insert(0, 'default')
    if profile.telegram_model and profile.telegram_model.provider == name:
        consumers.append('telegram')
    return consumers


def crossed_stages(before, after):
    if before is None:
        return []
    result = []
    for stage in STAGES:
        explicit = getattr(before.llm_routing, stage)
        partial = bool(explicit.provider) != bool(explicit.model)
        if partial and pair(resolve_stage(before.llm_routing, stage)) != pair(resolve_stage(after.llm_routing, stage)):
            result.append(stage)
    return result


def correct_stages(before, after, corrections):
    for stage, choice in corrections.items():
        if choice == 'previous':
            previous = resolve_stage(before.llm_routing, stage)
            getattr(after.llm_routing, stage).provider = previous.provider.name
            getattr(after.llm_routing, stage).model = previous.model
        elif choice == 'default':
            getattr(after.llm_routing, stage).provider = None
            getattr(after.llm_routing, stage).model = None
    return after


def telegram_impact(config, profile):
    selection = profile.telegram_model
    view = {'selection': selection.model_dump() if selection else None, 'admitted': False}
    if selection is None:
        return {**view, 'reason_code': 'telegram_selection_missing',
                'detail': 'No separate Telegram provider/model is selected; global defaults are not a fallback.'}
    if config is None:
        return {**view, 'reason_code': 'telegram_config_missing', 'detail': 'No saved configuration can resolve the separate Telegram route.'}
    try:
        provider = config.llm_routing.provider_by_name(selection.provider)
    except KeyError:
        return {**view, 'reason_code': 'telegram_provider_missing', 'detail': 'The selected Telegram provider is unavailable; its selection is retained unchanged.'}
    view['server'] = provider.base_url
    try:
        resolve_local(config, profile)  # Reuse actual strict Telegram admission, no client or I/O.
    except ValueError as error:
        reason = ('telegram_ai_disabled' if not config.llm_enabled else 'telegram_protocol_rejected'
                  if provider.type != 'openai' else 'telegram_endpoint_rejected')
        return {**view, 'reason_code': reason, 'detail': str(error)}
    return {**view, 'admitted': True, 'reason_code': '', 'detail': 'Separate literal-loopback OpenAI-compatible route; unchanged, not operationally tested.'}


def impact(before, after, profile, provider, *, source=None):
    stages = [{'stage': stage, 'before': pair(resolve_stage(before.llm_routing, stage)) if before else {'provider': None, 'model': None},
               'after': pair(resolve_stage(after.llm_routing, stage)),
               'explicit': getattr(before.llm_routing, stage).model_dump() if before else {},
               'after_explicit': getattr(after.llm_routing, stage).model_dump()}
              for stage in STAGES]
    telegram_view = telegram_impact(before, profile)
    return {'default': {'before': before.llm_routing.default.model_dump() if before else None,
                        'after': after.llm_routing.default.model_dump()},
            'stages': stages, 'telegram': telegram_view,
            'existing_server': {'provider': source, 'consumers': provider_consumers(after, profile, source) if source else [], 'changed': False},
            'shared_authorization': dependencies(before, profile, provider) if before else {'providers': [], 'consumers': []},
            'verified': False}
