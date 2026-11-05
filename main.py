import json
import os
import re
import time
import uuid
from collections import OrderedDict

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    InternalError,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_text_message
from a2a.utils.errors import ServerError
from dotenv import load_dotenv
from loguru import logger
from openai import OpenAIError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from liftline import (
    CURRENT_REQUEST,
    ConfigurationUnavailableError,
    KnowledgeUnavailableError,
    ModelResponseError,
    RequestObservability,
    evaluate_request,
    extract_request,
    load_skill_bundle,
)

load_dotenv()

logger.remove()
logger.add(
    sink=lambda msg: print(msg, end=""),
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="{time:DD-MM-YYYY at HH:mm:ss} | {level: <8} | {message}",
)

DEFAULT_PORT = 2009
PROTOCOL_VERSION = "0.3.0"
MAX_CONTEXTS = 1000
MAX_REMEMBERED_MESSAGES = 50
DEFAULT_NAME = "LiftLine Dispatch Advisor"
DEFAULT_DESCRIPTION = (
    "Evaluate fictional LiftLine delivery from Zava Regional depot to Riverside: loaded capacity, "
    "site forklift requirements, arrival slots, and EUR quotes. Uses a bundled dispatch Skill "
    "with synthetic data. Use after Zava identifies an eligible origin. Does not own Zava stock "
    "or approval rules and cannot book."
)
conversations: OrderedDict[str, list[str]] = OrderedDict()


def agent_name() -> str:
    return os.getenv("AGENT_NAME") or DEFAULT_NAME


def public_url() -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not base:
        app_name = os.getenv("CONTAINER_APP_NAME")
        dns_suffix = os.getenv("CONTAINER_APP_ENV_DNS_SUFFIX")
        if app_name and dns_suffix:
            base = f"https://{app_name}.{dns_suffix}"
        else:
            base = f"http://localhost:{os.getenv('PORT', DEFAULT_PORT)}"
    return base.rstrip("/") + "/"


class ChatExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input().strip()
        context_id = context.context_id
        history = conversations.get(context_id, []) if context_id else []
        if context_id and context_id in conversations:
            conversations.move_to_end(context_id)
        request_info = CURRENT_REQUEST.get()
        if request_info is None:
            request_info = RequestObservability(
                correlation_id=uuid.uuid4().hex,
                capability="unknown",
                started_at=time.perf_counter(),
            )
        try:
            bundle = load_skill_bundle()
            request_info.fixture_id = bundle.fixture.fixture_id
            fields = await extract_request(bundle.skill_text, history, text)
            result = evaluate_request(fields, bundle.fixture, text)
            reply = result.text
            request_info.result_status = result.status
        except KnowledgeUnavailableError as error:
            reply = (
                f"status=unavailable\nRequired Skill/reference unavailable: {error}.\n"
                "advisory_only=true"
            )
            request_info.result_status = "unavailable"
        except ConfigurationUnavailableError:
            logger.error(
                f"LiftLine model authentication unavailable "
                f"correlation_id={request_info.correlation_id}"
            )
            request_info.result_status = "unavailable"
            raise ServerError(error=InternalError()) from None
        except (OpenAIError, ModelResponseError) as error:
            logger.error(
                f"LiftLine model request failed correlation_id={request_info.correlation_id} "
                f"error_type={type(error).__name__}"
            )
            request_info.result_status = "unavailable"
            raise ServerError(error=InternalError()) from None
        finally:
            logger.info(
                "dispatch_evaluation "
                f"correlation_id={request_info.correlation_id} "
                f"capability={request_info.capability} "
                "skill_id=liftline-dispatch "
                f"reference_id={request_info.fixture_id} "
                f"result_status={request_info.result_status} "
                f"elapsed_ms={(time.perf_counter() - request_info.started_at) * 1000:.1f}"
            )
            if context_id:
                conversations[context_id] = [*history, text][-MAX_REMEMBERED_MESSAGES:]
                conversations.move_to_end(context_id)
                while len(conversations) > MAX_CONTEXTS:
                    conversations.popitem(last=False)
        await event_queue.enqueue_event(
            new_agent_text_message(reply, context_id=context.context_id)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        body = b"".join(chunks)
        request_info = self.request_info(scope, body)
        token = CURRENT_REQUEST.set(request_info)

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def track_response(message: Message) -> None:
            if message["type"] == "http.response.start":
                request_info.http_status = message["status"]
            await send(message)

        try:
            await self.app(scope, replay, track_response)
        except Exception:
            request_info.result_status = "unavailable"
            raise
        finally:
            logger.info(
                "a2a_request "
                f"correlation_id={request_info.correlation_id} "
                f"capability={request_info.capability} "
                f"result_status={request_info.result_status} "
                f"http_status={request_info.http_status or 'unknown'} "
                f"elapsed_ms={(time.perf_counter() - request_info.started_at) * 1000:.1f}"
            )
            CURRENT_REQUEST.reset(token)

    @staticmethod
    def request_info(scope: Scope, body: bytes) -> RequestObservability:
        correlation_id = uuid.uuid4().hex
        capability = "unknown"
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            payload = {}
        if isinstance(payload, dict):
            request_id = payload.get("id")
            params = payload.get("params")
            message = params.get("message") if isinstance(params, dict) else None
            context_id = message.get("contextId") if isinstance(message, dict) else None
            raw_id = request_id if request_id is not None else context_id
            if isinstance(raw_id, (str, int)):
                correlation_id = (
                    re.sub(r"[^A-Za-z0-9._:-]", "_", str(raw_id))[:64] or uuid.uuid4().hex
                )
            method = payload.get("method")
            if method in {"message/send", "message/stream"}:
                capability = method
        return RequestObservability(
            correlation_id=correlation_id,
            capability=capability,
            started_at=time.perf_counter(),
        )


def build_card() -> AgentCard:
    description = os.getenv("AGENT_DESCRIPTION") or DEFAULT_DESCRIPTION
    if not 30 <= len(description) <= 1000:
        logger.warning(
            f"AGENT_DESCRIPTION has {len(description)} characters; Copilot Studio needs 30-1000"
        )
    return AgentCard(
        name=agent_name(),
        description=description,
        url=public_url(),
        version="0.1.0",
        protocol_version=PROTOCOL_VERSION,
        preferred_transport="JSONRPC",
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="liftline-dispatch",
                name="LiftLine dispatch evaluation",
                description=(
                    "Evaluates fictional LiftLine delivery capacity, handling, arrival slots, "
                    "and EUR quotes for the Zava demo. Advisory only; does not book or approve."
                ),
                tags=["delivery", "dispatch", "quote", "synthetic-data"],
                examples=[
                    "Evaluate 60 bags of MORTAR-20 from ZAVA-DEPOT to Riverside renovation site "
                    "on 2030-07-14 in Europe/Amsterdam, before 10:00. No forklift is available; "
                    "use baseline handling assumptions.",
                    "What details do you need to evaluate this delivery?",
                ],
            )
        ],
    )


def build_app() -> Starlette:
    handler = DefaultRequestHandler(agent_executor=ChatExecutor(), task_store=InMemoryTaskStore())
    application = A2AStarletteApplication(agent_card=build_card(), http_handler=handler)
    app = application.build(middleware=[Middleware(RequestLogMiddleware)])
    # Some Copilot Studio connections send JSON-RPC to the discovery URL.
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
        app.routes.extend(
            route for route in application.routes(rpc_url=path) if route.methods == {"POST"}
        )
    return app


def main() -> None:
    port = int(os.getenv("PORT", DEFAULT_PORT))
    logger.info(f"Starting {agent_name()} on 0.0.0.0:{port}, card at {public_url()}")
    uvicorn.run(
        build_app(),
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level=os.getenv("LOG_LEVEL", "INFO").lower(),
    )


if __name__ == "__main__":
    main()
