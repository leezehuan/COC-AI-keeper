from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class SkillSpec:
    name: str
    action_types: list[str]
    allowed_tools: list[str]
    description: str
    constraints: list[str] = field(default_factory=list)


@dataclass
class SkillResult:
    skill: str
    input: dict[str, Any]
    observations: list[dict[str, Any]]
    result: dict[str, Any]
    success: bool = True
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "input": self.input,
            "observations": self.observations,
            "result": self.result,
            "success": self.success,
            "error": self.error,
        }


SkillHandler = Callable[[dict[str, Any]], SkillResult]
