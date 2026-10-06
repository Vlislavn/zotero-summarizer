import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import _map_reduce
from zotero_summarizer.services.library.quality_review import assess_digest
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


# Sanitized retained 210-character extraction; not proven historical model input.
DENIAL = ('Access Denied\nYou don\'t have permission to access "http://www.test.com/0000-0000/00/00/0000" '
          'on this server.\nReference #18.00000000.0000000000.0000000\n'
          'https://errors.edgesuite.net/18.00000000.0000000000.0000000')
XML_DENIAL = '<Error><Code>Denied</Code><Message>Accès refusé</Message><RequestId>example</RequestId></Error>'


class GeneratorReached(Exception):
    pass


class Generator:
    def pydantic_prompt(self, **kwargs):
        raise GeneratorReached


@pytest.mark.parametrize('text', [DENIAL, XML_DENIAL, DENIAL.replace('Access Denied', 'Accès refusé')])
def test_standalone_rejects_operational_original_before_generation(text):
    with pytest.raises(APIError, match='operational response'):
        assess_digest(title='Paper', full_text=text, config=_default_goals_config(), llm=Generator())


@pytest.mark.parametrize('strategy', ['rank', 'prefix', 'map_reduce'])
def test_dispatch_admits_original_before_any_strategy(strategy, monkeypatch):
    def reached(**kwargs):
        raise GeneratorReached

    monkeypatch.setattr(_map_reduce, 'assess_digest', reached)
    monkeypatch.setattr(_map_reduce, 'map_reduce_digest', reached)
    config = _default_goals_config()
    config.quality_review.chunk_strategy = strategy
    with pytest.raises(APIError):
        _map_reduce.digest_for_strategy('Paper', DENIAL, config, map_llm=Generator(),
                                      reduce_llm=Generator(), budget=_map_reduce.ChunkBudget(100, 100, 1))


@pytest.mark.parametrize('text', [
    'A qualitative comparison of verification and validation.',
    'Access Denied\nWe study denied access in healthcare, using interviews.',
    'An analysis of CDN errors. The following is a quoted template:\n' + DENIAL,
    'A paper on XML APIs quotes the response:\n' + XML_DENIAL,
    DENIAL + '\nDiscussion: This study compares access restrictions.',
])
def test_short_partial_access_themed_and_quoted_papers_reach_generation(text):
    with pytest.raises(GeneratorReached):
        assess_digest(title='Paper', full_text=text, config=_default_goals_config(), llm=Generator())


def test_admit_verification_original_not_generated_notes():
    with pytest.raises(GeneratorReached):
        assess_digest(title='Paper', full_text=DENIAL, verification_text='A short genuine paper.',
                      config=_default_goals_config(), llm=Generator())
    with pytest.raises(APIError):
        assess_digest(title='Paper', full_text='Generated notes', verification_text=DENIAL,
                      config=_default_goals_config(), llm=Generator())

LOGIN_HTML = '<html><body><form action="/login"><label>Username</label><input name="user"><label>Password</label><input type="password"><button>Sign in</button></form></body></html>'
LOGIN_TEXT = 'Sign in\nUsername\nPassword\nRemember me\nForgot password?\nSign in'


@pytest.mark.parametrize('text', [LOGIN_HTML, LOGIN_TEXT])
def test_whole_authentication_scaffolding_is_not_a_paper(text):
    with pytest.raises(APIError):
        assess_digest(title='Paper', full_text=text, config=_default_goals_config(), llm=Generator())


@pytest.mark.parametrize('text', [
    'Sign in: A study of password authentication.',
    'Our experiment quotes this login form:\n' + LOGIN_HTML,
    LOGIN_TEXT + '\nWe compare authentication methods.',
])
def test_login_themed_paper_and_embedded_form_are_admitted(text):
    with pytest.raises(GeneratorReached):
        assess_digest(title='Paper', full_text=text, config=_default_goals_config(), llm=Generator())
