from __future__ import annotations

import json
from dataclasses import dataclass

from .base import StructuredCall


@dataclass(slots=True)
class CodexPreparedRequest:
    call_kind: str
    prompt: str
    schema: dict
    use_output_schema: bool


def prepare_codex_request(call: StructuredCall, *, session_id: str | None, attempt_index: int) -> CodexPreparedRequest:
    prompt = call.prompt
    use_output_schema = session_id is None
    if not use_output_schema:
        prompt = _wrap_schema_in_prompt(call.prompt, _strip_schema_descriptions(call.schema))
    if attempt_index > 1:
        prompt = _retry_after_invalid_json_prompt(prompt)
    return CodexPreparedRequest(
        call_kind=call.call_kind,
        prompt=prompt,
        schema=call.schema,
        use_output_schema=use_output_schema,
    )


def _wrap_schema_in_prompt(prompt: str, schema: dict) -> str:
    return (
        prompt
        + "\n\nReturn only a single JSON object that matches this schema exactly. "
        + "Do not include markdown fences or any explanatory text.\nSchema:\n"
        + json.dumps(schema, separators=(",", ":"))
    )


def _retry_after_invalid_json_prompt(prompt: str) -> str:
    return (
        prompt
        + "\n\nYour previous reply was not valid JSON. Retry now. "
        + "Return only one strict JSON object with double-quoted property names and string values where required. "
        + "Do not include comments, markdown fences, trailing commas, or explanatory text."
    )


def _strip_schema_descriptions(value):
    if isinstance(value, dict):
        return {key: _strip_schema_descriptions(item) for key, item in value.items() if key != "description"}
    if isinstance(value, list):
        return [_strip_schema_descriptions(item) for item in value]
    return value
