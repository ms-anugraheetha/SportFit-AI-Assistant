"""Chat orchestration: deterministic routing and state machine, with the LLM used ONLY for general Q&A.

Security properties enforced here, in plain code:
  * Creating a request needs state == awaiting_submission_confirmation AND a message that is exactly
    a confirmation word. Nothing the model or a crafted message says can skip this.
  * create_service_request is never offered to the model, and the agent loop rejects any tool not on
    the allowlist.
  * The model never sees repair-form data: form turns are handled without it.
  * Failures are reported honestly; no tool result is ever fabricated.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app import repair_form as form
from app.agent import run_agent
from app.conversation import MAX_TURNS, Conversation, ConversationStore, ConvState
from app.llm import ChatLLM, LLMUnavailableError
from app.mcp_client import McpUnavailableError
from app.schemas import REFERENCE_RE

log = logging.getLogger("sportfit.chat")

REF_RE = re.compile(r"\bSFD-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}\b", re.I)
EDIT_RE = re.compile(r"^(?:edit|change|fix|correct)\s+(?:my\s+|the\s+)?(.+)$", re.I)
START_RE = re.compile(
    r"\b(submit|create|open|file|start|make|raise|log|book|request)\b.{0,40}\b(repair|service|ticket|request)\b"
    r"|\brepair request\b|\breport (a |an |the )?(fault|problem|issue)\b",
    re.I,
)
QUESTION_START_RE = re.compile(r"^\s*(how|what|where|when|why|which|does|do|is|are)\b", re.I)
TRACK_RE = re.compile(
    r"\btrack\b|status of|check (my|the) (request|ticket|repair)|where is my (request|repair)|my demo request", re.I
)
HANDOFF_RE = re.compile(
    r"\b(human|real person|live agent|customer service|representative|speak (to|with)|"
    r"talk to (a |an )?(person|human|someone|agent|staff)|call (you|them|sportfit)|"
    r"contact (you|sportfit|them|support|details)|phone number|support team)\b",
    re.I,
)
SAFETY_RE = re.compile(
    r"smok|burn|spark|shock|electr|\bfire\b|injur|hurt|unsafe|danger|collaps|snapp|crack|frayed|unstable|"
    r"wobbl|\bsafety\b|\bfell\b|exposed wire|melting",
    re.I,
)

CONFIRM_WORDS = {"confirm", "yes", "y", "submit", "yes confirm", "confirm submission", "i confirm", "yes submit"}
CANCEL_WORDS = {"cancel", "stop", "quit", "exit", "abort", "start over", "nevermind", "never mind"}
CANCEL_AT_CONFIRM = CANCEL_WORDS | {"no", "n", "dont", "don't"}
SKIP_WORDS = {"skip", "none", "no", "n/a", "na", "-", "nothing", "no thanks"}
HANDOFF_COMMANDS = {
    "human", "talk to a human", "talk to human", "speak to a human", "talk to a person",
    "speak to a person", "human support",
}

GENERAL_ACTIONS = ["Submit a repair request", "Track my demo request", "Talk to a human"]
DEMO_NOTE = (
    "This is demonstration data only. This independent academic prototype is not connected to "
    "SportFit's real service system."
)


@dataclass
class ChatResult:
    conversation_id: str
    state: ConvState
    reply: str
    suggested_actions: list[str] = field(default_factory=list)
    request_reference: str | None = None
    sources: list[dict[str, str]] = field(default_factory=list)
    safety_notice: list[str] = field(default_factory=list)
    degraded: bool = False


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9'/\- ]", " ", text.lower())).strip()


class ChatOrchestrator:
    def __init__(self, mcp: Any, llm: ChatLLM | None, store: ConversationStore, max_tool_iterations: int = 4) -> None:
        self.mcp = mcp
        self.llm = llm
        self.store = store
        self.max_tool_iterations = max_tool_iterations

    # ------------------------------------------------------------------ entry point

    async def handle(self, conversation_id: str | None, text: str) -> ChatResult:
        conv = self.store.get_or_create(conversation_id)
        conv.turns += 1
        if conv.turns > MAX_TURNS:
            return self._result(conv, "This conversation has reached its length limit. Please refresh the page to start a new one.")
        safety = await self._safety_notice(text)
        result = await self._dispatch(conv, text, _norm(text))
        result.safety_notice = safety
        return result

    async def _dispatch(self, conv: Conversation, text: str, norm: str) -> ChatResult:
        if conv.state == ConvState.collecting_repair_details:
            return await self._collect(conv, text, norm)
        if conv.state == ConvState.awaiting_submission_confirmation:
            return await self._confirm(conv, text, norm)
        if conv.state == ConvState.tracking_request:
            if norm in CANCEL_WORDS:
                conv.state = ConvState.general
                return self._result(conv, "Okay, I've stopped the tracking lookup.", GENERAL_ACTIONS)
            if not REF_RE.search(text) and not START_RE.search(text) and norm not in HANDOFF_COMMANDS:
                return self._result(
                    conv,
                    "Please enter your demonstration request reference, which looks like SFD-XXXX-XXXX-XXXX (or type \"cancel\").",
                    ["cancel"],
                )
        return await self._route(conv, text, norm)

    # ------------------------------------------------------------------ helpers

    def _result(
        self,
        conv: Conversation,
        reply: str,
        actions: list[str] | None = None,
        *,
        reference: str | None = None,
        sources: list[dict[str, str]] | None = None,
        degraded: bool = False,
    ) -> ChatResult:
        return ChatResult(
            conversation_id=conv.id,
            state=conv.state,
            reply=reply,
            suggested_actions=actions or [],
            request_reference=reference,
            sources=sources or [],
            degraded=degraded,
        )

    def _unavailable(self, conv: Conversation) -> ChatResult:
        return self._result(
            conv,
            "Sorry - a backend service is temporarily unavailable, so I couldn't complete that. Nothing was changed. Please try again in a moment.",
            degraded=True,
        )

    async def _tool(self, name: str, args: dict[str, Any] | None = None, *, retry: bool = True) -> dict[str, Any]:
        return await self.mcp.call_tool(name, args or {}, retry=retry)

    async def _safety_notice(self, text: str) -> list[str]:
        if not SAFETY_RE.search(text):
            return []
        try:
            res = await self._tool("search_knowledge_base", {"query": text, "category": "safety"})
            matches = res.get("matches") or []
            if not matches:
                res = await self._tool("search_knowledge_base", {"query": "stop using damaged unsafe equipment", "category": "safety"})
                matches = res.get("matches") or []
        except McpUnavailableError:
            return []
        return [f"{m['title']}: {m['content']}" for m in matches[:2]]

    # ------------------------------------------------------------------ routing (general-like states)

    async def _route(self, conv: Conversation, text: str, norm: str) -> ChatResult:
        ref = REF_RE.search(text)
        if ref:
            return await self._lookup(conv, ref.group(0))
        if norm in CONFIRM_WORDS and conv.state == ConvState.request_created and conv.last_reference:
            return self._result(
                conv,
                f"That request is already recorded (reference {conv.last_reference}). Nothing more was saved.",
                GENERAL_ACTIONS,
                reference=conv.last_reference,
            )
        is_question = text.strip().endswith("?") and QUESTION_START_RE.match(text)
        if START_RE.search(text) and not is_question:
            return self._start_form(conv)
        if TRACK_RE.search(text) and not is_question:
            conv.state = ConvState.tracking_request
            return self._result(
                conv,
                "Sure. Please enter your demonstration request reference (it looks like SFD-XXXX-XXXX-XXXX). "
                "Only requests created in this demo can be tracked.",
                ["cancel"],
            )
        if HANDOFF_RE.search(text) or norm in HANDOFF_COMMANDS:
            return await self._handoff(conv)
        return await self._general(conv, text)

    # ------------------------------------------------------------------ general Q&A (LLM + MCP tools)

    async def _general(self, conv: Conversation, text: str) -> ChatResult:
        if conv.state != ConvState.general:
            conv.state = ConvState.general
        if self.llm is None or not self.mcp.connected:
            return await self._fallback(conv, text)
        try:
            result = await run_agent(self.llm, self.mcp, conv.history[-8:], text, self.max_tool_iterations)
        except (LLMUnavailableError, McpUnavailableError):
            return await self._fallback(conv, text)
        conv.history += [{"role": "user", "content": text}, {"role": "assistant", "content": result.text}]
        conv.history = conv.history[-8:]
        return self._result(conv, result.text, GENERAL_ACTIONS, sources=result.sources)

    async def _fallback(self, conv: Conversation, text: str) -> ChatResult:
        """No model available: return the closest verified knowledge-base entries, clearly labelled."""
        try:
            res = await self._tool("search_knowledge_base", {"query": text[:200]})
        except McpUnavailableError:
            return self._unavailable(conv)
        matches = (res.get("matches") or [])[:2]
        if not matches:
            return self._result(
                conv,
                "I can't verify an answer to that right now. You can rephrase, or type \"talk to a human\" for SportFit's published contact details.",
                GENERAL_ACTIONS,
                degraded=True,
            )
        body = "\n\n".join(f"{m['title']}\n{m['content']}" for m in matches)
        sources = [{"title": m["title"], "url": m["source_url"]} for m in matches if m.get("source_url")]
        return self._result(
            conv,
            "The AI model is unavailable right now, so here is the closest verified information:\n\n" + body,
            GENERAL_ACTIONS,
            sources=sources,
            degraded=True,
        )

    # ------------------------------------------------------------------ tracking and handoff

    async def _lookup(self, conv: Conversation, reference: str) -> ChatResult:
        try:
            res = await self._tool("get_service_request", {"request_reference": reference})
        except McpUnavailableError:
            return self._unavailable(conv)
        conv.state = ConvState.general
        if not res.get("ok"):
            return self._unavailable(conv)
        if not res.get("found"):
            return self._result(
                conv,
                "No demonstration request was found for that reference. Please check it and try again. "
                "Only requests created in this demo can be tracked.\n\n" + DEMO_NOTE,
                GENERAL_ACTIONS,
            )
        req = res.get("request") or {}
        reply = (
            f"Reference {req.get('request_reference', reference.upper())}\n"
            f"• Status: {req.get('status_label', req.get('status', 'unknown'))}\n"
            f"• Equipment: {req.get('equipment_type', '-')}\n"
            f"• Created: {str(req.get('created_at', ''))[:10]}\n"
            f"• Last updated: {str(req.get('updated_at', ''))[:10]}\n\n{DEMO_NOTE}"
        )
        return self._result(conv, reply, GENERAL_ACTIONS)

    async def _handoff(self, conv: Conversation) -> ChatResult:
        try:
            res = await self._tool("get_handoff_guidance")
        except McpUnavailableError:
            return self._unavailable(conv)
        conv.state = ConvState.handoff
        entries = res.get("entries") or []
        if not res.get("ok") or not entries:
            return self._result(
                conv,
                "I can't show verified contact details right now. Please use the contact details published on SportFit's official website.",
                GENERAL_ACTIONS,
            )
        sources = [{"title": e["title"], "url": e["source_url"]} for e in entries if e.get("source_url")]
        return self._result(conv, entries[0]["content"], GENERAL_ACTIONS, sources=sources)

    # ------------------------------------------------------------------ guided repair form

    def _start_form(self, conv: Conversation) -> ChatResult:
        conv.reset_form()
        conv.state = ConvState.collecting_repair_details
        conv.asking = form.next_field(conv.draft, conv.skipped)
        intro = (
            "Sure - I'll collect the details for a DEMONSTRATION repair request. This is an academic prototype: "
            "nothing is sent to SportFit. Please don't share payment details, ID numbers or passwords. "
            'You can type "cancel" at any time, or "help" for hints.\n\n'
        )
        return self._ask(conv, prefix=intro)

    def _ask(self, conv: Conversation, prefix: str = "") -> ChatResult:
        assert conv.asking is not None
        actions = ["cancel"]
        if form.is_optional(conv.asking, conv.draft):
            actions = ["skip", "skip all", "cancel"]
        elif conv.asking == "preferred_contact_method":
            actions = ["email", "phone", "WhatsApp", "cancel"]
        return self._result(conv, prefix + form.QUESTIONS[conv.asking], actions)

    def _after_value(self, conv: Conversation, ack: str = "") -> ChatResult:
        conv.bad_attempts = 0
        nxt = form.next_field(conv.draft, conv.skipped)
        if nxt is not None:
            conv.asking = nxt
            return self._ask(conv, prefix=ack)
        return self._summary(conv)

    def _summary(self, conv: Conversation) -> ChatResult:
        from pydantic import ValidationError

        from app.schemas import ServiceRequestCreate

        try:
            ServiceRequestCreate.model_validate(conv.draft)
        except ValidationError as exc:  # should not happen: every value was validated individually
            for err in exc.errors():
                if err["loc"]:
                    conv.draft.pop(str(err["loc"][0]), None)
            conv.asking = form.next_field(conv.draft, conv.skipped) or "customer_name"
            return self._ask(conv, prefix="One of the details didn't pass a final check. ")
        conv.state = ConvState.awaiting_submission_confirmation
        conv.asking = None
        return self._result(conv, form.build_summary(conv.draft), ["confirm", "cancel"])

    def _edit_field(self, conv: Conversation, target: str) -> ChatResult | None:
        for word in re.findall(r"[a-z]+", target.lower()):
            field_name = form.ALIASES.get(word)
            if field_name:
                conv.state = ConvState.collecting_repair_details
                conv.asking = field_name
                conv.skipped.discard(field_name)
                return self._ask(conv, prefix=f"Okay, let's update {form.LABELS[field_name].lower()}. ")
        return None

    async def _collect(self, conv: Conversation, text: str, norm: str) -> ChatResult:
        if norm in CANCEL_WORDS:
            conv.reset_form()
            conv.state = ConvState.general
            return self._result(conv, "Okay, I've cancelled that. Nothing was saved.", GENERAL_ACTIONS)
        if norm in HANDOFF_COMMANDS:
            conv.reset_form()
            return await self._handoff(conv)
        edit = EDIT_RE.match(text.strip())
        if edit:
            res = self._edit_field(conv, edit.group(1))
            if res:
                return res
        if conv.asking is None:
            conv.asking = form.next_field(conv.draft, conv.skipped)
            if conv.asking is None:
                return self._summary(conv)
        asking = conv.asking
        if norm == "help":
            return self._ask(conv, prefix="Here's the question again. ")
        if norm == "skip all":
            for f in form.OPTIONAL_FIELDS:
                if form.is_optional(f, conv.draft) and f not in conv.draft:
                    conv.skipped.add(f)
            return self._after_value(conv, "Okay, skipping the optional details. ")
        if norm in SKIP_WORDS and form.is_optional(asking, conv.draft):
            conv.skipped.add(asking)
            conv.draft.pop(asking, None)
            return self._after_value(conv)
        if norm == "skip" and not form.is_optional(asking, conv.draft):
            return self._result(conv, "That one is required to record the request. " + form.QUESTIONS[asking], ["cancel"])

        value, err = form.validate_field(asking, text)
        if err or value is None:
            conv.bad_attempts += 1
            more = ""
            if conv.bad_attempts >= 4:
                more = ' If you are stuck, type "cancel" or "talk to a human".'
            return self._result(conv, f"{err} {form.QUESTIONS[asking]}{more}", ["cancel"])
        conv.draft[asking] = value
        conv.skipped.discard(asking)
        return self._after_value(conv)

    # ------------------------------------------------------------------ confirmation gate

    async def _confirm(self, conv: Conversation, text: str, norm: str) -> ChatResult:
        if norm in CONFIRM_WORDS:
            return await self._create(conv)
        if norm in CANCEL_AT_CONFIRM:
            conv.reset_form()
            conv.state = ConvState.general
            return self._result(conv, "Okay, I've discarded it. Nothing was saved.", GENERAL_ACTIONS)
        edit = EDIT_RE.match(text.strip())
        if edit:
            res = self._edit_field(conv, edit.group(1))
            if res:
                return res
        return self._result(
            conv,
            'I need an explicit confirmation before anything is saved. Reply "confirm" to record this demonstration request, '
            '"cancel" to discard it, or "edit <field>" (for example "edit email") to change something.',
            ["confirm", "cancel"],
        )

    async def _create(self, conv: Conversation) -> ChatResult:
        # Safe to call here: state is awaiting_submission_confirmation and the text was an exact confirmation.
        try:
            res = await self._tool("create_service_request", dict(conv.draft), retry=False)
        except McpUnavailableError:
            return self._result(
                conv,
                'I couldn\'t save the request because a backend service is unavailable. Your details are still here - reply "confirm" to try again.',
                ["confirm", "cancel"],
                degraded=True,
            )
        reference = res.get("request_reference")
        if res.get("ok") is True and isinstance(reference, str) and REFERENCE_RE.match(reference):
            conv.last_reference = reference
            conv.reset_form()
            conv.state = ConvState.request_created
            reply = (
                "Your demonstration repair request has been recorded.\n"
                f"Reference: {reference}\n\n"
                "Important: it was saved only in this academic prototype's database. It was NOT sent to SportFit, "
                "and no technician will contact you. For real service, contact SportFit directly "
                '(type "talk to a human" for their published details).\n'
                'You can check this demo request any time with "Track my demo request".'
            )
            return self._result(conv, reply, GENERAL_ACTIONS, reference=reference)
        if res.get("error") == "validation_error":
            bad = [d.get("field") for d in res.get("details", []) if d.get("field") in form.ORDER]
            for f in bad:
                conv.draft.pop(f, None)
            conv.state = ConvState.collecting_repair_details
            conv.asking = form.next_field(conv.draft, conv.skipped) or "customer_name"
            return self._ask(conv, prefix="Some details didn't pass the final check, so nothing was saved. ")
        return self._result(
            conv,
            'Sorry, the request could not be saved, so no reference was issued. Your details are still here - reply "confirm" to try again.',
            ["confirm", "cancel"],
            degraded=True,
        )
