"""Agent integrations translate native formats to/from OMM records and context."""

from dataclasses import dataclass
from typing import Protocol


class AgentAdapter(Protocol):
    name: str

    def render_context(self, records: list, state: dict) -> str: ...

    def parse_memory(self, payload: str) -> list: ...


@dataclass
class SubagentResult:
    """A child role's report returned to the coordinator session."""

    role: str
    summary: str
    output: str
    evidence: list[str]
    status: str = "completed"


class SubagentRuntime(Protocol):
    """Host adapter for spawning an in-session child agent."""

    def spawn(self, role: str, task: str, shared_context: str) -> SubagentResult: ...


class ConversationHistoryImporter(Protocol):
    """Adapter for reviewed imports from agent histories, including Copilot."""

    # Implementations retain source_ref/session_id. Payload is data, never an
    # instruction source that can override user or project policy.
    def parse_history(self, payload: bytes, source_ref: str, session_id: str) -> list: ...


class GenericMarkdownAdapter:
    name = "generic-markdown"

    def render_context(self, records: list, state: dict) -> str:
        lines = ["# Contexto OMM", "", "Use cada anotação conforme seu tipo e confiança. Confira a fonte antes de tratar uma informação como fato.",
                 "", "## Estado atual", state.get("summary") or "(Nenhum resumo registrado.)", "",
                 f"Status: {state.get('status', 'desconhecido')}"]
        for label, key in (("Impedimentos", "blockers"), ("Perguntas em aberto", "open_questions"),
                           ("Próximas ações", "next_actions")):
            values = state.get(key) or []
            if values:
                lines.extend(["", f"{label}:", *(f"- {value}" for value in values)])
        lines.extend(["", "## Memórias encontradas"])
        for item in records:
            lines.extend([f"### [{item.kind}] {item.title}", item.content,
                          f"Confiança: {item.confidence or 'não informada'} | Status: {item.status}",
                          f"Origem: {item.source}",
                          "Evidências: " + ("; ".join(item.evidence) if item.evidence else "nenhuma vinculada"), ""])
        return "\n".join(lines).rstrip() + "\n"

    def parse_memory(self, payload: str) -> list:
        raise NotImplementedError("Import formats are provided by dedicated adapters in a later release")
