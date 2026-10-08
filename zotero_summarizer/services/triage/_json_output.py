import json
from collections.abc import Iterator
from typing import TypeVar

from pydantic import BaseModel, ValidationError

Model = TypeVar("Model", bound=BaseModel)


def _outer_json(text: str) -> Iterator[str]:
    start = None
    stack = []
    quoted = escaped = False
    for index, char in enumerate(text):
        if start is None:
            if char in "{[":
                start = index
                stack.append(char)
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            opener = stack.pop()
            if (opener, char) not in (("{", "}"), ("[", "]")):
                start = None
                stack.clear()
            elif not stack:
                yield text[start:index + 1]
                start = None


def parse_typed_output(text: str, model: type[Model]) -> Model:
    """Validate whole JSON or one unambiguous outer root; allow identical repeats."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    else:
        return model.model_validate(payload)

    selected = None
    selected_payload = None
    failure = None
    for candidate in _outer_json(text):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        try:
            validated = model.model_validate(payload)
        except ValidationError as exc:
            if failure is None:
                failure = exc
            continue
        if selected is not None and payload != selected_payload:
            raise ValueError("Ambiguous schema-valid JSON roots in LLM output")
        selected, selected_payload = validated, payload
    if selected is not None:
        return selected
    if failure is not None:
        raise failure
    raise ValueError("Could not parse JSON content from LLM output")
