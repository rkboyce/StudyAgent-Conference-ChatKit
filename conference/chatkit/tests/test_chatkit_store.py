import asyncio
from datetime import datetime

import pytest
from chatkit.store import NotFoundError
from chatkit.types import ThreadMetadata

from study_chat.chatkit_server import ChatRequestContext, SQLiteChatKitStore


def test_chatkit_threads_are_isolated_by_controller_session(tmp_path):
    store = SQLiteChatKitStore(tmp_path / "chat.db")
    owner = ChatRequestContext("session-owner")
    other = ChatRequestContext("session-other")
    thread = ThreadMetadata(id="thread-1", created_at=datetime.now())
    asyncio.run(store.save_thread(thread, owner))
    assert asyncio.run(store.load_thread("thread-1", owner)).id == "thread-1"
    with pytest.raises(NotFoundError):
        asyncio.run(store.load_thread("thread-1", other))
