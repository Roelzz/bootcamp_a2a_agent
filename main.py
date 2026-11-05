import json
import os
from collections import OrderedDict

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, UnsupportedOperationError
from a2a.utils import new_agent_text_message
from a2a.utils.errors import ServerError
from dotenv import load_dotenv
from loguru import logger
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
DEFAULT_NAME = "Bootcamp Echo Agent"
DEFAULT_DESCRIPTION = (
    "Generic conversational agent for the A2A bootcamp lab. It greets you, explains what it "
    "can do, and replies to every message while remembering the conversation."
)
GREETING_WORDS = {"hi", "hello", "hey", "hallo", "hoi"}
HELP_PHRASES = ("help", "what can you do", "who are you", "what are you")
HIDDEN_HEADERS = {"authorization", "cookie"}

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


def respond(history: list[str], text: str) -> str:
    message = text.strip()
    if not message:
        return "I didn't receive any text. Send me a message and I'll reply."
    lowered = message.lower()
    if any(phrase in lowered for phrase in HELP_PHRASES):
        return (
            f"I'm {agent_name()}, a generic A2A test agent. I greet you, remember the "
            "conversation, and echo what you say. Try 'hello' or send me anything."
        )
    if lowered.split()[0].strip("!,.") in GREETING_WORDS:
        return f"Hello! I'm {agent_name()}. Say 'help' to see what I can do."
    count = min(len(history) + 1, MAX_REMEMBERED_MESSAGES)
    noun = "message" if count == 1 else "messages"
    return f'You said: "{message}". I now remember {count} {noun} from this conversation.'


class ChatExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        context_id = context.context_id or ""
        text = context.get_user_input()
        history = conversations.setdefault(context_id, [])
        conversations.move_to_end(context_id)
        reply = respond(history, text)
        history.append(text)
        del history[:-MAX_REMEMBERED_MESSAGES]
        while len(conversations) > MAX_CONTEXTS:
            conversations.popitem(last=False)
        logger.info(f"Replying in context {context_id} ({len(history)} remembered)")
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
        self.log_request(scope, body)

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    def log_request(scope: Scope, body: bytes) -> None:
        path = scope["path"]
        headers = {
            key.decode(): value.decode()
            for key, value in scope["headers"]
            if key.decode().lower() not in HIDDEN_HEADERS
        }
        logger.debug(f"POST {path} headers={headers} body={body.decode(errors='replace')}")
        try:
            payload = json.loads(body)
        except ValueError:
            logger.warning(f"POST {path}: body is not valid JSON")
            return
        if not isinstance(payload, dict):
            logger.warning(f"POST {path}: JSON-RPC body is not an object")
            return
        params = payload.get("params")
        message = params.get("message") if isinstance(params, dict) else None
        context_id = message.get("contextId") if isinstance(message, dict) else None
        logger.info(f"POST {path} method={payload.get('method')} contextId={context_id}")


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
                id="chat",
                name="General chat",
                description="Greets the user, explains what it can do, and replies to any message.",
                tags=["chat", "echo"],
                examples=["Hello", "What can you do?"],
            )
        ],
    )


def build_app() -> Starlette:
    handler = DefaultRequestHandler(agent_executor=ChatExecutor(), task_store=InMemoryTaskStore())
    application = A2AStarletteApplication(agent_card=build_card(), http_handler=handler)
    return application.build(middleware=[Middleware(RequestLogMiddleware)])


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
