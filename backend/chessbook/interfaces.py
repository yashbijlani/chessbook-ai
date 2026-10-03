"""Future-proof extension points (charter #37).

Nothing here is required in V1.  The defaults are deterministic; a local model
(llama.cpp / Ollama / a local vision model) can be dropped in later without
touching the pipeline, because every consumer takes the *interface*, never a
concrete provider.

There is deliberately no code path that reads an API key, a hostname, or a
token.  Adding one would violate the product's core constraint.
"""

from __future__ import annotations

import abc
from typing import Any, Protocol

import chess


class ExplanationProvider(abc.ABC):
    """Explain a position in natural language."""

    name: str = "abstract"

    @abc.abstractmethod
    def explain_position(
        self, board: chess.Board, context: str | None = None, style: str = "concise"
    ) -> str: ...

    def available(self) -> bool:
        return False


class DiagramRecognizer(abc.ABC):
    """Read a position off a diagram image."""

    name: str = "abstract"

    @abc.abstractmethod
    def recognize(self, image_path: str, hints: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return ``{"fen": str|None, "confidence": float, "method": str}``."""


class EngineProvider(abc.ABC):
    """Local chess engine (Stockfish and friends).  Optional, never mandatory."""

    name: str = "abstract"

    @abc.abstractmethod
    def analyse(self, fen: str, depth: int = 14) -> dict[str, Any]: ...

    def available(self) -> bool:
        return False


class DeterministicExplainer(ExplanationProvider):
    """The default: no model, just a factual description of the position."""

    name = "deterministic"

    def available(self) -> bool:
        return True

    def explain_position(
        self, board: chess.Board, context: str | None = None, style: str = "concise"
    ) -> str:
        if board.is_checkmate():
            side = "White" if board.turn == chess.BLACK else "Black"
            return f"Checkmate. {side} has no legal move."
        if board.is_stalemate():
            return "Stalemate: no legal moves and the side to move is not in check."
        if board.is_insufficient_material():
            return "Insufficient material to mate: the position is drawn."
        side = "White" if board.turn == chess.WHITE else "Black"
        checks = board.checkers()
        parts = [f"{side} to move"]
        if checks:
            parts.append("in check")
        if board.is_kingside_castling_impossible() is False and board.has_castling_rights(
            chess.WHITE
        ):
            parts.append("White may castle")
        if board.has_castling_rights(chess.BLACK):
            parts.append("Black may castle")
        if board.is_capture():
            parts.append("at least one capture is available")
        return "; ".join(parts) + "."


class NullDiagramRecognizer(DiagramRecognizer):
    """The default: we never guess a position off a picture."""

    name = "null"

    def available(self) -> bool:
        return False

    def recognize(self, image_path: str, hints: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "fen": None,
            "confidence": 0.0,
            "method": "not_implemented",
            "note": (
                "V1 derives diagram positions from the surrounding move text. "
                "A local vision model can be plugged in via DiagramRecognizer."
            ),
        }


class NullEngine(EngineProvider):
    name = "null"

    def analyse(self, fen: str, depth: int = 14) -> dict[str, Any]:
        raise NotImplementedError("No local engine is configured")


class ProviderRegistry(Protocol):
    explainer: ExplanationProvider
    recognizer: DiagramRecognizer
    engine: EngineProvider


class Providers:
    """Holds the active providers.  Defaults are the deterministic ones."""

    def __init__(self) -> None:
        self.explainer: ExplanationProvider = DeterministicExplainer()
        self.recognizer: DiagramRecognizer = NullDiagramRecognizer()
        self.engine: EngineProvider = NullEngine()

    def describe(self) -> dict[str, Any]:
        return {
            "explainer": {"name": self.explainer.name, "available": self.explainer.available()},
            "diagram_recognizer": {
                "name": self.recognizer.name,
                "available": self.recognizer.available(),
            },
            "engine": {"name": self.engine.name, "available": self.engine.available()},
        }


PROVIDERS = Providers()


def register_explainer(provider: ExplanationProvider) -> None:
    PROVIDERS.explainer = provider


def register_recognizer(recognizer: DiagramRecognizer) -> None:
    PROVIDERS.recognizer = recognizer


def register_engine(engine: EngineProvider) -> None:
    PROVIDERS.engine = engine