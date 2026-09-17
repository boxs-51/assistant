from fastapi import APIRouter, Depends, HTTPException, Response, status
from datetime import datetime, timezone
import uuid
from .....application.container import ApplicationContainer
from .....domain.schemas.capability import SessionMessageEditRequest, SessionRegenerateRequest
from .....domain.schemas.identity import Identity
from .....domain.schemas.event import BaseEvent
from ...authentication.dependency import get_current_identity
from ...dependencies import get_container

router = APIRouter(prefix="/v1/sessions", tags=["Sessions"])

async def _owned_session(container, session_id, identity):
    async with container.uow_factory() as uow:
        session = await uow.sessions.get_by_id(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        if session.user_id != identity.user_id:
            raise HTTPException(status_code=403, detail="Session access denied.")
        return session, await uow.sessions.get_messages_by_session_id(session_id)

def _message(item):
    return {
        "id": item.id,
        "role": item.role,
        "content": item.content,
        "turn_id": item.turn_id,
        "sequence": item.sequence,
        "created_at": item.created_at,
        "completed_at": item.completed_at,
    }

@router.get("")
async def list_sessions(identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    async with container.uow_factory() as uow:
        sessions = await uow.sessions.list_by_user_id(identity.user_id)
        return [{"session_id": item.id, "title": item.title, "status": item.status, "created_at": item.created_at, "updated_at": item.updated_at} for item in sessions]

@router.get("/{session_id}")
async def get_session(session_id: str, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    session, messages = await _owned_session(container, session_id, identity)
    return {"session_id": session.id, "user_id": session.user_id, "organization_id": session.organization_id, "status": session.status, "title": session.title, "created_at": session.created_at, "updated_at": session.updated_at, "messages": [_message(item) for item in messages]}

@router.get("/{session_id}/messages")
async def list_session_messages(session_id: str, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    _, messages = await _owned_session(container, session_id, identity)
    return [_message(item) for item in messages]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    async with container.uow_factory() as uow:
        deleted = await uow.sessions.delete_owned_session(
            session_id,
            identity.user_id,
        )
        if not deleted:
            # Do not disclose whether a foreign-owned session exists.
            raise HTTPException(status_code=404, detail="Session not found.")
        await uow.commit()
    await container.event_bus.publish(
        BaseEvent(
            event_name="session.deleted",
            session_id=session_id,
            payload={"session_id": session_id, "owner_user_id": identity.user_id},
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.patch("/{session_id}/messages/{message_id}")
async def edit_session_message(session_id: str, message_id: str, body: SessionMessageEditRequest, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    await _owned_session(container, session_id, identity)
    async with container.uow_factory() as uow:
        message = await uow.sessions.update_message_content(session_id, message_id, body.content)
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        await uow.commit()
        return _message(message)

@router.post("/{session_id}/regenerate")
async def regenerate_session_response(session_id: str, body: SessionRegenerateRequest, identity: Identity = Depends(get_current_identity), container: ApplicationContainer = Depends(get_container)):
    """Ask the provider again using the persisted, possibly edited transcript."""
    _, messages = await _owned_session(container, session_id, identity)
    request_messages = []
    for item in messages:
        content = item.content
        if isinstance(content, dict) and content.get("type") == "text":
            content = content.get("data", "")
        request_messages.append({"role": item.role, "content": content})
    payload = {
        "model": body.model, "messages": request_messages, "session_id": session_id,
        "config": body.config, "metadata": body.metadata, "tools": body.tools or None,
    }
    response = await container.provider_runtime.chat_handler.execute_with_fallback(
        container.http_client, payload
    )
    response_payload = response.model_dump(mode="json")
    choices = response_payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is not None:
            async with container.uow_factory() as uow:
                await uow.sessions.add_message(
                    session_id=session_id, role=message.get("role", "assistant"),
                    content={"type": "text", "data": content},
                    turn_id=f"turn_{uuid.uuid4().hex}",
                    completed_at=datetime.now(timezone.utc),
                )
                await uow.commit()
    return response_payload
