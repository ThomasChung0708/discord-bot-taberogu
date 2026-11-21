"""Shared Chat Completions settings and bounded parameter compatibility retries."""

import logging
import os
import re

from openai import APIStatusError


logger = logging.getLogger(__name__)
_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}
_OPTIONAL_PARAMETERS = {"reasoning_effort", "temperature", "response_format"}
# Process-local compatibility knowledge; never written to configuration or disk.
_REJECTED_PARAMETERS: dict[str, set[str]] = {}


def get_openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-6-luna").strip() or "gpt-6-luna"


def get_reasoning_effort(value: str | None = None) -> str:
    if value is None:
        value = os.getenv("OPENAI_REASONING_EFFORT", "none")
    effort = value.strip().lower() or "none"
    if effort not in _REASONING_EFFORTS:
        logger.warning("Invalid OPENAI_REASONING_EFFORT; falling back to none")
        return "none"
    return effort


def _unsupported_parameter(error: APIStatusError) -> str | None:
    body = error.body if isinstance(error.body, dict) else {}
    details = body.get("error", body)
    if not isinstance(details, dict):
        details = body
    message = str(details.get("message") or error).lower()
    code = details.get("code") or error.code
    if code not in {"unsupported_parameter", "unknown_parameter"} and not re.search(
        r"unsupported|(?:unknown|unrecognized)\s+(?:request\s+)?(?:parameter|argument)\b"
        r"|not supported|does not support",
        message,
    ):
        return None
    parameter = details.get("param") or error.param
    if parameter:
        return parameter if parameter in _OPTIONAL_PARAMETERS else None
    for candidate in sorted(_OPTIONAL_PARAMETERS):
        if re.search(rf"\b{candidate}\b", message):
            return candidate
    return None


def create_chat_completion(
    client,
    *,
    messages,
    temperature=None,
    max_completion_tokens,
    response_format=None,
    timeout=20,
    model=None,
    reasoning_effort=None,
):
    """Return the SDK response; retry only explicitly rejected optional parameters.

    The completion budget covers visible output and any reasoning tokens.
    Required request fields are never removed to make a request succeed.
    """
    effective_model = (model.strip() if model is not None else "") or get_openai_model()
    effort = get_reasoning_effort(reasoning_effort)
    kwargs = {
        "model": effective_model,
        "messages": messages,
        "max_completion_tokens": max_completion_tokens,
        "reasoning_effort": effort,
        "timeout": timeout,
    }
    if effort == "none" and temperature is not None:
        kwargs["temperature"] = temperature
    if response_format is not None:
        kwargs["response_format"] = response_format
    rejected_parameters = _REJECTED_PARAMETERS.get(effective_model, set())
    for parameter in rejected_parameters:
        kwargs.pop(parameter, None)
    if ("reasoning_effort" not in kwargs and temperature is not None
            and "temperature" not in rejected_parameters):
        kwargs["temperature"] = temperature

    # Each retry removes one of three optional fields, so this loop is bounded.
    rejected = None
    while True:
        try:
            return client.chat.completions.create(**kwargs)
        except APIStatusError as exc:
            parameter = _unsupported_parameter(exc) if exc.status_code == 400 else None
            if parameter is None:
                raise
            if parameter not in kwargs:
                if rejected:
                    raise RuntimeError(
                        f"OpenAI request for model {effective_model} failed after "
                        f"removing rejected parameter {rejected}"
                    ) from exc
                raise
            rejected_parameters = _REJECTED_PARAMETERS.setdefault(effective_model, set())
            rejected_parameters.add(parameter)
            del kwargs[parameter]
            if (parameter == "reasoning_effort" and temperature is not None
                    and "temperature" not in rejected_parameters):
                kwargs["temperature"] = temperature
            rejected = parameter
            logger.warning(
                "OpenAI rejected parameter %s for model %s; retrying without it",
                parameter, effective_model,
            )
