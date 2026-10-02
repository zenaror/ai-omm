"""Agent integrations translate native formats to/from OMM records and context."""

from dataclasses import dataclass
from typing import Protocol


class AgentAdapter(Protocol):
    name: str

    def render_context(self, records: list, state: dict, max_chars: int = 5000) -> str: ...

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

    def render_context(self, records: list, state: dict, max_chars: int = 5000) -> str:
        """Render a short, relevance-ordered prompt block within a character budget."""
        output = "# Contexto OMM\n\nConfira as fontes antes de tratar algo como fato.\n"

        def append(block: str) -> bool:
            nonlocal output
            remaining = max_chars - len(output)
            if len(block) <= remaining:
                output += block
                return True
            marker = "\n[Contexto reduzido para caber no limite.]\n"
            if remaining > len(marker):
                output += block[:remaining - len(marker)].rstrip() + marker
            return False

        state_lines = ["\n## Estado\n", str(state.get("summary") or "(Sem resumo registrado.)") + "\n",
                       f"Status: {state.get('status', 'desconhecido')}\n"]
        for label, key in (("Impedimentos", "blockers"), ("Perguntas em aberto", "open_questions"),
                           ("Próximas ações", "next_actions")):
            values = state.get(key) or []
            if values:
                state_lines.extend([f"\n{label}:\n", *(f"- {value}\n" for value in values[:5])])
        if not append("".join(state_lines)):
            return output[:max_chars]
        if not append("\n## Memórias relevantes\n"):
            return output[:max_chars]
        for item in records:
            evidence = "; ".join(item.evidence[:3]) if item.evidence else "nenhuma vinculada"
            block = (f"\n### [{item.kind}] {item.title}\n"
                     f"Origem: {item.source} | Confiança: {item.confidence or 'não informada'}\n"
                     f"Evidências: {evidence}\n{item.content}\n")
            if not append(block):
                break
        return (output.rstrip() + "\n")[:max_chars]

    def parse_memory(self, payload: str) -> list:
        raise NotImplementedError("Import formats are provided by dedicated adapters in a later release")
