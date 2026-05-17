from collections.abc import AsyncIterator
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LlmAdapter(Protocol):
    async def structured(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        response_model: type[T],
        model: str,
        **kwargs: Any,
    ) -> T: ...

    def stream(
        self,
        *,
        slot: str,
        variables: dict[str, Any],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[str]: ...
