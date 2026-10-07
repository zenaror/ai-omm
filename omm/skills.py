"""Read and resolve portable OMM skill inheritance declarations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


@dataclass(frozen=True)
class SkillDocument:
    name: str
    scope: str
    description: str
    inherits: tuple[str, ...]
    content: str


def _frontmatter(content: str) -> tuple[list[str], str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], content
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        raise ValueError("O frontmatter da skill não tem o delimitador de fechamento '---'.") from None
    return lines[1:end], "\n".join(lines[end + 1:]).lstrip("\n")


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_skill(content: str, folder_name: str) -> SkillDocument:
    """Parse the small, stable YAML subset used by skill frontmatter."""
    lines, _ = _frontmatter(content)
    name_match = re.search(r"^name:\s*(.+?)\s*$", "\n".join(lines), re.MULTILINE)
    description_match = re.search(r"^description:\s*(.*?)\s*$", "\n".join(lines), re.MULTILINE)
    scope_match = re.search(r"^\s+scope:\s*(.+?)\s*$", "\n".join(lines), re.MULTILINE)
    name = _scalar(name_match.group(1)) if name_match else folder_name
    description = _scalar(description_match.group(1)) if description_match else ""
    scope = _scalar(scope_match.group(1)) if scope_match else "unspecified"

    metadata_index = next((index for index, line in enumerate(lines)
                           if re.fullmatch(r"metadata:\s*", line)), None)
    inherits: list[str] = []
    if metadata_index is not None:
        metadata_end = metadata_index + 1
        while metadata_end < len(lines):
            line = lines[metadata_end]
            if line and not line[0].isspace():
                break
            metadata_end += 1
        metadata_lines = lines[metadata_index + 1:metadata_end]
        inherit_index = next((index for index, line in enumerate(metadata_lines)
                              if re.fullmatch(r"\s{2}inherits:\s*.*", line)), None)
        if inherit_index is not None:
            declaration = metadata_lines[inherit_index].split(":", 1)[1].strip()
            if declaration:
                if not (declaration.startswith("[") and declaration.endswith("]")):
                    raise ValueError(f"A lista inherits da skill {name} precisa ser uma lista YAML.")
                inherits = [_scalar(item.strip()) for item in declaration[1:-1].split(",") if item.strip()]
            else:
                for line in metadata_lines[inherit_index + 1:]:
                    if not line.strip():
                        continue
                    if re.match(r"^\s{2}\S", line):
                        break
                    if not re.match(r"^\s{4}-\s+", line):
                        raise ValueError(f"Item inválido na lista inherits da skill {name}.")
                    inherits.append(_scalar(re.sub(r"^\s{4}-\s+", "", line)))

    if not _SKILL_NAME.fullmatch(name):
        raise ValueError(f"Nome inválido no frontmatter da skill: {name!r}.")
    if len(inherits) != len(set(inherits)):
        raise ValueError(f"A skill {name} declara a mesma skill-pai mais de uma vez.")
    invalid = [parent for parent in inherits if not _SKILL_NAME.fullmatch(parent)]
    if invalid:
        raise ValueError(f"Nome inválido em inherits da skill {name}: {invalid[0]!r}.")
    if name in inherits:
        raise ValueError(f"A skill {name} não pode herdar de si mesma.")
    return SkillDocument(name, scope, description, tuple(inherits), content)


def _skill_path(root: Path, name: str) -> Path:
    if not _SKILL_NAME.fullmatch(name):
        raise ValueError("Nome de skill inválido.")
    skills_root = (root / "skills").resolve()
    candidate = root / "skills" / name / "SKILL.md"
    path = candidate.resolve()
    if path.parent.parent != skills_root or path.parent.name != name or not path.is_file():
        raise ValueError(f"Skill não encontrada ou fora da pasta skills/: {name}")
    return path


def read_skill(root: Path, name: str) -> SkillDocument:
    path = _skill_path(root, name)
    document = parse_skill(path.read_text(encoding="utf-8"), name)
    if document.name != name:
        raise ValueError(f"O nome no frontmatter de {name} não corresponde ao nome da pasta.")
    return document


def resolve_skill_chain(root: Path, name: str,
                        candidate: tuple[str, str] | None = None) -> list[SkillDocument]:
    """Return parents before child, once each; reject missing parents and cycles."""
    ordered: list[SkillDocument] = []
    completed: set[str] = set()
    visiting: list[str] = []

    def visit(current_name: str) -> None:
        if current_name in visiting:
            cycle = " → ".join([*visiting, current_name])
            raise ValueError(f"Herança circular entre skills: {cycle}.")
        if current_name in completed:
            return
        if candidate and current_name == candidate[0]:
            document = parse_skill(candidate[1], current_name)
        else:
            document = read_skill(root, current_name)
        visiting.append(current_name)
        for parent in document.inherits:
            visit(parent)
        visiting.pop()
        completed.add(current_name)
        ordered.append(document)

    visit(name)
    return ordered


def render_resolved_skill(root: Path, name: str) -> str:
    chain = resolve_skill_chain(root, name)
    names = " → ".join(document.name for document in chain)
    blocks = [f"# Skills aplicáveis: {chain[-1].name}\n\n"+
              f"Herança resolvida pela OMM: {names}.\n\n"
              "A ordem abaixo é da base mais geral até a especialização solicitada.\n"]
    for document in chain:
        _, body = _frontmatter(document.content)
        blocks.append(f"\n---\n\n<!-- Início da skill: {document.name} -->\n\n"
                      f"{body}\n\n<!-- Fim da skill: {document.name} -->\n")
    return "".join(blocks).rstrip() + "\n"


def skill_catalog(root: Path) -> list[SkillDocument]:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return []
    documents = []
    for path in sorted(skills_root.glob("*/SKILL.md")):
        try:
            if path.is_symlink() or path.parent.is_symlink():
                continue
            documents.append(parse_skill(path.read_text(encoding="utf-8"), path.parent.name))
        except (OSError, ValueError):
            continue
    return documents


def validate_skill_candidate(root: Path, name: str, content: str) -> None:
    """Validate a new/updated skill's full inheritance before writing it."""
    chain = resolve_skill_chain(root, name, candidate=(name, content))
    if chain[-1].name != name:
        raise ValueError("O campo name do frontmatter precisa corresponder ao nome da skill salva.")
