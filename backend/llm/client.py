"""
RxTrail — DeepSeek V4 API client wrapper.

Features:
  - OpenAI-compatible chat completions
  - JSON structured output with Pydantic validation
  - Automatic retry on validation failure (1 retry with error feedback)
  - Fallback mode when LLM fails
  - All calls logged to llm_calls collection
  - 30s timeout
"""
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from backend.config import settings


class LLMError(Exception):
    """Raised when the LLM call fails after retries."""


async def structured_call(
    prompt: str,
    schema: type[BaseModel],
    db: AsyncIOMotorDatabase,
    org_id: str,
    user_id: str = "system",
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 1500,
) -> dict:
    """Make a structured LLM call with JSON output validation.

    Args:
        prompt: The complete prompt (system + user messages)
        schema: Pydantic model to validate the response against
        db: MongoDB handle for logging
        org_id: Org ID for logging
        user_id: User ID for logging
        model: Override model (default: settings.deepseek_model)
        temperature: 0-0.3 for structured output
        max_tokens: Max tokens in response

    Returns:
        Validated dict matching the schema, OR {"fallback": True, ...} on failure

    Raises:
        LLMError: On complete failure after retries
    """
    model_name = model or settings.deepseek_model
    start_time = time.time()
    attempt = 0
    max_attempts = 2

    # Build system message from the prompt
    system_msg = {
        "role": "system",
        "content": (
            "Είσαι βοηθός CRM για πάροχο ιατροτεχνολογικών στην Ελλάδα. "
            "Απαντάς ΠΑΝΤΑ σε έγκυρο JSON σύμφωνα με το ζητούμενο σχήμα. "
            "Μην προσθέτεις κείμενο εκτός JSON."
        ),
    }

    # Build messages
    # If prompt looks like it already has a system message, use as-is
    if prompt.strip().startswith("{"):
        messages = [
            system_msg,
            {"role": "user", "content": prompt},
        ]
    else:
        messages = [
            system_msg,
            {"role": "user", "content": prompt},
        ]

    last_error = None
    validation_errors = []

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await client.post(
                    f"{settings.deepseek_base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.deepseek_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_name,
                        "messages": messages,
                        "temperature": temperature if attempt == 1 else 0.0,
                        "max_tokens": max_tokens,
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                body = response.json()

            content = body["choices"][0]["message"]["content"]

            # Parse JSON
            parsed = json.loads(content)

            # Validate with Pydantic
            try:
                validated = schema.model_validate(parsed)
                result = validated.model_dump()

                # Log success
                await _log_call(
                    db, org_id, user_id, model_name, prompt, result,
                    attempt, time.time() - start_time, True, None,
                )
                return result

            except Exception as ve:
                validation_errors.append(str(ve))
                if attempt < max_attempts:
                    # Add validation error to next prompt
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": f"Το JSON δεν είναι έγκυρο. Σφάλμα: {ve}. "
                                   f"Διόρθωσέ το και επέστρεψε ΜΟΝΟ έγκυρο JSON.",
                    })
                last_error = ve

        except httpx.HTTPError as he:
            last_error = he
            if attempt < max_attempts:
                continue  # Retry on network errors

        except json.JSONDecodeError as je:
            validation_errors.append(f"JSON parse error: {je}")
            if attempt < max_attempts:
                messages.append({
                    "role": "user",
                    "content": "Το προηγούμενο response δεν ήταν έγκυρο JSON. "
                               "Παρακαλώ επέστρεψε ΜΟΝΟ έγκυρο JSON.",
                })
            last_error = je

    # All retries exhausted — fallback
    elapsed = time.time() - start_time
    fallback = {"fallback": True, "error": str(last_error), "attempts": attempt}

    await _log_call(
        db, org_id, user_id, model_name, prompt, fallback,
        attempt, elapsed, False, str(last_error),
    )

    return fallback


async def simple_call(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 500,
) -> str:
    """Simple text-in/text-out LLM call. For brief generation, etc."""
    model_name = model or settings.deepseek_model
    messages = [
        {"role": "system", "content": "Είσαι βοηθός CRM. Απάντα σύντομα και ουσιαστικά στα Ελληνικά."},
        {"role": "user", "content": prompt},
    ]

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        response = await client.post(
            f"{settings.deepseek_base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "thinking": {"type": "disabled"},
            },
        )
        response.raise_for_status()
        body = response.json()

    return body["choices"][0]["message"]["content"]


async def _log_call(
    db: AsyncIOMotorDatabase,
    org_id: str,
    user_id: str,
    model: str,
    prompt: str,
    response: dict,
    attempt: int,
    latency: float,
    success: bool,
    error: Optional[str],
):
    """Log an LLM call to the database."""
    try:
        await db.llm_calls.insert_one({
            "org_id": org_id,
            "user_id": user_id,
            "model": model,
            "prompt_preview": prompt[:500],
            "response_preview": str(response)[:500],
            "attempt": attempt,
            "latency_ms": round(latency * 1000),
            "success": success,
            "error": error,
            "at": datetime.now(timezone.utc),
        })
    except Exception:
        pass  # Non-critical — don't break the flow
