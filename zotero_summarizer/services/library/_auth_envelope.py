"""Recognize complete authentication control scaffolding, not academic prose."""
from html.parser import HTMLParser


_CONTROLS = frozenset({
    'sign in', 'log in', 'login', 'username', 'email', 'email address', 'password',
    'remember me', 'forgot password?', 'forgot your password?', 'forgot password',
})
_ACTIONS = frozenset({'sign in', 'log in', 'login'})


def _control_lines(lines: list[str]) -> bool:
    labels = {line.strip().casefold() for line in lines if line.strip()}
    return bool(labels & _ACTIONS) and 'password' in labels and bool(
        labels & {'username', 'email', 'email address'}
    ) and labels <= _CONTROLS


class _LoginPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.labels: list[str] = []
        self.forms = 0
        self.password = False
        self.unknown = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.forms += tag == 'form'
        self.password |= tag == 'input' and values.get('type', '').casefold() == 'password'
        self.unknown |= tag not in {
            'html', 'head', 'title', 'body', 'main', 'div', 'span', 'form', 'label',
            'input', 'button', 'a', 'br', 'p', 'h1', 'h2', 'meta', 'link',
        }

    def handle_data(self, data):
        self.labels.extend(data.splitlines())


def authentication_envelope(text: str) -> bool:
    if not text.startswith('<'):
        return _control_lines(text.splitlines())
    page = _LoginPage()
    page.feed(text)
    page.close()
    return page.forms == 1 and page.password and not page.unknown and _control_lines(page.labels)
