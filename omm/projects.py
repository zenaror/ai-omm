"""Explicit project names and aliases; historical files remain at their original paths."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .errors import OMMError

_SIMPLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}")


class ProjectCatalog:
    def __init__(self, root: Path):
        path = root / 'memory' / 'projects.json'
        try:
            if path.is_symlink():
                raise ValueError('symlink')
            data = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            data = {'schema_version': 1, 'projects': {}, 'aliases': {}}
        except (OSError, ValueError) as exc:
            raise OMMError('INVALID_ARGUMENT', 'O cadastro de projetos não pôde ser lido.',
                           'Confira memory/projects.json; não ignore o cadastro para gravar em outro projeto.') from exc
        def require(condition):
            if not condition:
                raise ValueError("invalid catalog")

        try:
            require(isinstance(data, dict) and data.get('schema_version') == 1)
            self.projects = data.get('projects', {})
            self.aliases = data.get('aliases', {})
            require(isinstance(self.projects, dict) and isinstance(self.aliases, dict))
            for name, item in self.projects.items():
                require(isinstance(name, str) and _SIMPLE.fullmatch(name) and isinstance(item, dict))
                require(isinstance(item.get('display_name', name), str))
                require(item.get('category', 'project') in {'project', 'experiment', 'shared'})
            for alias, target in self.aliases.items():
                require(isinstance(alias, str) and alias and len(alias) <= 1000)
                require(not any(ord(c) < 32 for c in alias))
                require(isinstance(target, str) and _SIMPLE.fullmatch(target))
                require(alias != target and target not in self.aliases)
                require(alias not in {'global', 'default'})
                require(target not in {'global', 'default'})
        except (ValueError, TypeError, AttributeError) as exc:
            raise OMMError('INVALID_ARGUMENT', 'O cadastro de projetos contém nomes ou aliases inválidos.',
                           'Use destinos canônicos simples, sem ciclos, e preserve os históricos.') from exc

    def canonical(self, scope: str) -> str:
        return self.aliases.get(scope, scope)

    def write_scope(self, scope: str) -> str:
        canonical = self.canonical(scope)
        if not isinstance(canonical, str) or not _SIMPLE.fullmatch(canonical):
            raise OMMError('INVALID_ARGUMENT', 'Informe o identificador canônico do projeto.',
                           'Não use caminho, URL ou nome de repositório como um projeto novo.')
        return canonical

    def family(self, scope: str) -> list[str]:
        canonical = self.canonical(scope)
        return [canonical, *sorted(alias for alias, target in self.aliases.items() if target == canonical)]

    def expand(self, scopes: list[str] | None) -> list[str] | None:
        return None if scopes is None else list(dict.fromkeys(
            name for scope in scopes for name in self.family(scope)))

    def merged_source(self, source: str) -> str:
        """Resolve a missing historical path after an explicit physical scope merge."""
        parts = source.split("/")
        if len(parts) < 3 or parts[0] != "sources":
            return source
        alias = parts[1]
        if alias not in self.aliases or not _SIMPLE.fullmatch(alias):
            return source
        return "/".join(["sources", self.aliases[alias], "legacy-" + alias, *parts[2:]])

    def label(self, scope: str, fallback: str | None = None) -> str:
        return self.projects.get(self.canonical(scope), {}).get('display_name', fallback or scope)

    def category(self, scope: str) -> str:
        return self.projects.get(self.canonical(scope), {}).get(
            'category', 'shared' if scope == 'global' else 'project')
