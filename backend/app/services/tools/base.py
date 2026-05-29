from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    constraints: list[str] = field(default_factory=list)


@dataclass
class ToolObservation:
    tool: str
    input: dict[str, Any]
    output: dict[str, Any]
    success: bool = True
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "input": self.input,
            "output": self.output,
            "success": self.success,
            "error": self.error,
        }


ToolHandler = Callable[[dict[str, Any]], ToolObservation]
