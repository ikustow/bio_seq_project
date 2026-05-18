from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .tools import build_context_tools


class SuggestedQuestionsOutput(BaseModel):
    questions: list[str] = Field(
        min_length=3,
        max_length=3,
        description="Exactly three concise follow-up questions.",
    )

    @field_validator("questions")
    @classmethod
    def validate_questions(cls, value: list[str]) -> list[str]:
        normalized = normalize_questions(value)
        if len(normalized) != 3:
            raise ValueError("Expected exactly three non-empty unique questions.")
        return normalized


class SuggestedQuestionsAgent:
    def __init__(self, model: Any) -> None:
        self._model = model

    def generate(
        self,
        *,
        user_message: str,
        assistant_message: str,
        history: list[dict[str, Any]],
        selected_candidate: dict[str, Any] | None,
    ) -> SuggestedQuestionsOutput:
        from langchain.agents import create_agent

        tools = build_context_tools(
            selected_candidate=selected_candidate,
            history=history,
            assistant_message=assistant_message,
        )
        agent = create_agent(
            model=self._model,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            response_format=SuggestedQuestionsOutput,
        )
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": build_user_prompt(user_message, assistant_message),
                    }
                ]
            }
        )
        structured = result.get("structured_response")
        if isinstance(structured, SuggestedQuestionsOutput):
            return structured
        if isinstance(structured, dict):
            return SuggestedQuestionsOutput.model_validate(structured)

        messages = result.get("messages") or []
        content = getattr(messages[-1], "content", "") if messages else ""
        return SuggestedQuestionsOutput(questions=_questions_from_text(content))


def normalize_questions(values: list[Any], *, limit: int = 3) -> list[str]:
    questions: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _clean_question(str(value or ""))
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        questions.append(text)
        if len(questions) >= limit:
            break
    return questions


def _questions_from_text(content: Any) -> list[str]:
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text") or item.get("content") or item)
            if isinstance(item, dict)
            else str(item)
            for item in content
        )
    text = str(content or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("questions"), list):
        return normalize_questions(data["questions"])
    if isinstance(data, list):
        return normalize_questions(data)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line) for line in lines]
    return normalize_questions(candidates)


def _clean_question(text: str) -> str:
    cleaned = " ".join(text.strip().strip("\"'`").split())
    cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", cleaned)
    if not cleaned:
        return ""
    if len(cleaned) > 180:
        cleaned = cleaned[:177].rstrip() + "..."
    return cleaned
