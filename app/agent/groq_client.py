"""Small, lazy ChatGroq adapter for structured planning output."""

from threading import Lock
from typing import Protocol, TypeVar

from langchain_core.runnables import Runnable
from pydantic import BaseModel, ValidationError
from langchain_groq import ChatGroq

from app.core.config import Settings, get_settings
from app.core.exceptions import AIProviderError

ModelT = TypeVar("ModelT", bound=BaseModel)


class StructuredChatModel(Protocol):
    """Async structured model interface consumed by application services."""

    async def ainvoke(self, input: object) -> object: ...


class ChatModelProvider(Protocol):
    """Factory interface for provider-independent structured chat models."""

    def structured_model(
        self,
        schema: type[ModelT],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StructuredChatModel: ...


class GroqChatModelProvider:
    """Create and reuse one configured ChatGroq client and schema runnable."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self._clients: dict[tuple[float, int | None], ChatGroq] = {}
        self._models: dict[tuple[type[BaseModel], float, int | None], Runnable] = {}
        self._lock = Lock()

    def structured_model(
        self,
        schema: type[ModelT],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StructuredChatModel:
        with self._lock:
            settings = self._settings or get_settings()
            selected_temperature = (
                settings.groq_planning_temperature
                if temperature is None
                else temperature
            )
            selected_max_tokens = (
                settings.groq_max_output_tokens
                if max_tokens is None
                else max_tokens
            )
            cache_key = (schema, selected_temperature, selected_max_tokens)
            model = self._models.get(cache_key)
            if model is not None:
                return model
            client = self._get_client(
                selected_temperature,
                selected_max_tokens,
            )
            model = client.with_structured_output(
                schema,
                method="function_calling",
            )
            self._models[cache_key] = model
            return model

    def _get_client(
        self,
        temperature: float,
        max_tokens: int | None,
    ) -> ChatGroq:
        client_key = (temperature, max_tokens)
        if client_key in self._clients:
            return self._clients[client_key]
        try:
            settings = self._settings or get_settings()
        except ValidationError:
            raise AIProviderError(
                "AI provider configuration is invalid."
            ) from None

        if not settings.groq_api_key or not settings.groq_api_key.strip():
            raise AIProviderError(
                "AI provider configuration is incomplete."
            )
        if not settings.groq_model or not settings.groq_model.strip():
            raise AIProviderError(
                "AI provider configuration is incomplete."
            )

        try:
            client = ChatGroq(
                model=settings.groq_model,
                api_key=settings.groq_api_key,
                temperature=temperature,
                timeout=settings.groq_request_timeout_seconds,
                max_retries=settings.groq_max_retries,
                max_tokens=max_tokens,
            )
        except (TypeError, ValueError):
            raise AIProviderError(
                "AI provider configuration is invalid."
            ) from None
        self._clients[client_key] = client
        return client
