"""Self-hosted ChatKit adapter.

ChatKit preserves conversation presentation; the StudyAgent controller remains
the only authority for workflow transitions, review policies, and approval.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from agents import Agent, RunContextWrapper, Runner, function_tool
from chatkit.agents import AgentContext, simple_to_agent_input, stream_agent_response
from chatkit.store import NotFoundError, Store
from chatkit.types import (
    Attachment,
    Page,
    ThreadItem,
    ThreadMetadata,
    ThreadStreamEvent,
    UserMessageItem,
)
from chatkit.server import ChatKitServer
from pydantic import TypeAdapter

from .controller import Controller


@dataclass(frozen=True)
class ChatRequestContext:
    """Associates every ChatKit request with one opaque controller session."""

    session_id: str


class SQLiteChatKitStore(Store[ChatRequestContext]):
    """A small durable ChatKit store with strict per-session thread isolation."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS chat_threads "
            "(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, data TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS chat_items "
            "(thread_id TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, "
            "PRIMARY KEY(thread_id, id))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS chat_attachments "
            "(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, data TEXT NOT NULL)"
        )
        self.db.commit()
        self._item_adapter = TypeAdapter(ThreadItem)

    def _thread_row(self, thread_id: str, context: ChatRequestContext) -> tuple[str] | None:
        return self.db.execute(
            "SELECT data FROM chat_threads WHERE id=? AND session_id=?",
            (thread_id, context.session_id),
        ).fetchone()

    async def load_thread(self, thread_id: str, context: ChatRequestContext) -> ThreadMetadata:
        row = self._thread_row(thread_id, context)
        if not row:
            raise NotFoundError(thread_id)
        return ThreadMetadata.model_validate_json(row[0])

    async def save_thread(self, thread: ThreadMetadata, context: ChatRequestContext) -> None:
        existing = self.db.execute("SELECT session_id FROM chat_threads WHERE id=?", (thread.id,)).fetchone()
        if existing and existing[0] != context.session_id:
            raise NotFoundError(thread.id)
        self.db.execute(
            "INSERT OR REPLACE INTO chat_threads(id,session_id,data) VALUES(?,?,?)",
            (thread.id, context.session_id, thread.model_dump_json()),
        )
        self.db.commit()

    async def load_thread_items(self, thread_id: str, after: str | None, limit: int, order: str, context: ChatRequestContext) -> Page[ThreadItem]:
        await self.load_thread(thread_id, context)
        rows = self.db.execute("SELECT id,data FROM chat_items WHERE thread_id=? ORDER BY rowid ASC", (thread_id,)).fetchall()
        if after:
            positions = [index for index, row in enumerate(rows) if row[0] == after]
            rows = rows[(positions[0] + 1 if positions else 0):]
        if order == "desc":
            rows = list(reversed(rows))
        page_rows, remaining = rows[:limit], rows[limit:]
        return Page(data=[self._item_adapter.validate_json(row[1]) for row in page_rows], has_more=bool(remaining), after=page_rows[-1][0] if page_rows else None)

    async def load_threads(self, limit: int, after: str | None, order: str, context: ChatRequestContext) -> Page[ThreadMetadata]:
        rows = self.db.execute("SELECT id,data FROM chat_threads WHERE session_id=? ORDER BY rowid ASC", (context.session_id,)).fetchall()
        if after:
            positions = [index for index, row in enumerate(rows) if row[0] == after]
            rows = rows[(positions[0] + 1 if positions else 0):]
        if order == "desc":
            rows = list(reversed(rows))
        page_rows, remaining = rows[:limit], rows[limit:]
        return Page(data=[ThreadMetadata.model_validate_json(row[1]) for row in page_rows], has_more=bool(remaining), after=page_rows[-1][0] if page_rows else None)

    async def add_thread_item(self, thread_id: str, item: ThreadItem, context: ChatRequestContext) -> None:
        await self.save_item(thread_id, item, context)

    async def save_item(self, thread_id: str, item: ThreadItem, context: ChatRequestContext) -> None:
        await self.load_thread(thread_id, context)
        if item.thread_id != thread_id:
            raise ValueError("thread_item_mismatch")
        self.db.execute("INSERT OR REPLACE INTO chat_items(thread_id,id,data) VALUES(?,?,?)", (thread_id, item.id, item.model_dump_json()))
        self.db.commit()

    async def load_item(self, thread_id: str, item_id: str, context: ChatRequestContext) -> ThreadItem:
        await self.load_thread(thread_id, context)
        row = self.db.execute("SELECT data FROM chat_items WHERE thread_id=? AND id=?", (thread_id, item_id)).fetchone()
        if not row:
            raise NotFoundError(item_id)
        return self._item_adapter.validate_json(row[0])

    async def delete_thread(self, thread_id: str, context: ChatRequestContext) -> None:
        await self.load_thread(thread_id, context)
        self.db.execute("DELETE FROM chat_items WHERE thread_id=?", (thread_id,))
        self.db.execute("DELETE FROM chat_threads WHERE id=? AND session_id=?", (thread_id, context.session_id))
        self.db.commit()

    async def delete_thread_item(self, thread_id: str, item_id: str, context: ChatRequestContext) -> None:
        await self.load_thread(thread_id, context)
        self.db.execute("DELETE FROM chat_items WHERE thread_id=? AND id=?", (thread_id, item_id))
        self.db.commit()

    async def save_attachment(self, attachment: Attachment, context: ChatRequestContext) -> None:
        self.db.execute("INSERT OR REPLACE INTO chat_attachments(id,session_id,data) VALUES(?,?,?)", (attachment.id, context.session_id, attachment.model_dump_json()))
        self.db.commit()

    async def load_attachment(self, attachment_id: str, context: ChatRequestContext) -> Attachment:
        row = self.db.execute("SELECT data FROM chat_attachments WHERE id=? AND session_id=?", (attachment_id, context.session_id)).fetchone()
        if not row:
            raise NotFoundError(attachment_id)
        return Attachment.model_validate_json(row[0])

    async def delete_attachment(self, attachment_id: str, context: ChatRequestContext) -> None:
        self.db.execute("DELETE FROM chat_attachments WHERE id=? AND session_id=?", (attachment_id, context.session_id))
        self.db.commit()

    def purge_sessions(self, session_ids: list[str]) -> None:
        if not session_ids: return
        marks=",".join("?" for _ in session_ids)
        self.db.execute(f"DELETE FROM chat_items WHERE thread_id IN (SELECT id FROM chat_threads WHERE session_id IN ({marks}))", session_ids)
        self.db.execute(f"DELETE FROM chat_threads WHERE session_id IN ({marks})", session_ids)
        self.db.execute(f"DELETE FROM chat_attachments WHERE session_id IN ({marks})", session_ids)
        self.db.commit()


def build_agent(controller: Controller, model: str) -> Agent[AgentContext[ChatRequestContext]]:
    @function_tool
    def current_workflow_state(ctx: RunContextWrapper[AgentContext[ChatRequestContext]]) -> str:
        """Return the current confirmed workflow state for this participant."""
        state = controller.state(ctx.context.request_context.session_id)
        safe = {key: state.get(key) for key in ("workflow_type", "study_intent", "confirmed_role_statements", "active_role", "current_stage", "selected_phenotypes", "confirmed_scope", "approval_record", "generated_artifact_references")}
        return json.dumps(safe, sort_keys=True)

    @function_tool
    async def workflow_context_guidance(ctx: RunContextWrapper[AgentContext[ChatRequestContext]], question: str) -> str:
        """Return ACP-grounded, read-only guidance for the participant's current workflow stage."""
        result = await controller.contextual_guidance(ctx.context.request_context.session_id, question)
        return json.dumps(result.get("dialogue") or result, sort_keys=True)

    return Agent[AgentContext[ChatRequestContext]](
        name="OHDSI Study Agent",
        model=model,
        instructions=(
            "You are a conference demonstration guide for OHDSI study design. "
            "Use current_workflow_state before describing what has been decided. "
            "For every substantive workflow question, call workflow_context_guidance before answering and explain only its grounded guidance plus the confirmed state. "
            "Never infer or claim clinical approval, do not select a phenotype, confirm scope, approve concepts, emit artifacts, edit Atlas, or apply phenotype improvements. "
            "Those actions are explicitly performed through server-validated controls. "
            "Explain that no patient-level data or PHI may be entered. "
            "Phenotype improvement suggestions are advisory only and must be applied manually in Atlas3."
        ),
        tools=[current_workflow_state, workflow_context_guidance],
    )


class StudyChatKitServer(ChatKitServer[ChatRequestContext]):
    def __init__(self, store: SQLiteChatKitStore, controller: Controller, model: str):
        super().__init__(store)
        self.agent = build_agent(controller, model)

    async def respond(self, thread: ThreadMetadata, input_user_message: UserMessageItem | None, context: ChatRequestContext) -> AsyncIterator[ThreadStreamEvent]:
        items = await self.store.load_thread_items(thread.id, after=None, limit=40, order="desc", context=context)
        agent_context = AgentContext(thread=thread, store=self.store, request_context=context)
        result = Runner.run_streamed(self.agent, await simple_to_agent_input(list(reversed(items.data))), context=agent_context)
        async for event in stream_agent_response(agent_context, result):
            yield event
