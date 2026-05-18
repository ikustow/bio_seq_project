from __future__ import annotations

SYSTEM_PROMPT = """You generate exactly three concise follow-up questions for a protein sequence analysis chat.

Rules:
- Use only the current conversation, selected protein context, and assistant's latest answer.
- Match the user's language when it is clear.
- Each question must be a useful next user prompt, not an explanation.
- Do not duplicate the user's latest question.
- Do not promise a new database search.
- Keep questions short enough to fit as UI chips.
- Prefer diverse angles: function, domains, evidence, disease, pathways, interactions, or limitations.
- Call the available context tools before producing the final structured answer.
"""


def build_user_prompt(user_message: str, assistant_message: str) -> str:
    return (
        "Generate three likely follow-up questions for this chat turn.\n\n"
        f"Latest user message:\n{user_message.strip() or '(empty)'}\n\n"
        f"Latest assistant answer:\n{assistant_message.strip() or '(empty)'}"
    )
