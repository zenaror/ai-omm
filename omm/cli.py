from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from .models import KINDS, MemoryRecord
from .service import OMM
from .diagnostics import diagnose
from .history import accept_history, list_history_imports, show_history_import, stage_history
from .copilot_archive import import_copilot_chat
from .claude_archive import import_claude_session
from .restore import RestoreError, restore_from_git
from .sync import SyncError, preview_sync, sync_with_backup_report
from .backup_worker import BackupError


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="omm", description="Memória de projeto compartilhada entre assistentes")
    p.add_argument("--root", type=Path, default=Path.cwd(), help="pasta do projeto OMM (padrão: pasta atual)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="preparar os arquivos de memória e a busca")
    doctor = sub.add_parser("doctor", help="verificar se os arquivos, a busca e o backup estão prontos")
    doctor.add_argument("--json", action="store_true", help="mostrar o resultado em JSON")
    remember = sub.add_parser("remember", help="guardar uma informação na memória do projeto")
    remember.add_argument("--kind", required=True, choices=sorted(KINDS), help="tipo da informação")
    remember.add_argument("--title", required=True, help="título curto para encontrar a anotação depois")
    remember.add_argument("--content", required=True, help="informação que será guardada")
    remember.add_argument("--source", required=True, help="arquivo ou conversa de onde veio a informação")
    remember.add_argument("--evidence", action="append", default=[], help="evidência que apoia a anotação; pode ser usada mais de uma vez")
    remember.add_argument("--tag", action="append", default=[], help="rótulo para organizar anotações; pode ser usado mais de uma vez")
    remember.add_argument("--role", help="papel do agente que criou a anotação")
    remember.add_argument("--created-by", default="human", help="quem está guardando a informação")
    remember.add_argument("--confidence", help="nível de confiança, se conhecido")
    remember.add_argument("--session-id", help="identificador da conversa de origem")
    remember.add_argument("--workstream-id", help="frente de trabalho relacionada")
    remember.add_argument("--scope", default="default", help="alcance da anotação: global ou identificador do projeto")
    search = sub.add_parser("search", help="procurar informações na memória")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--workstream-id")
    search.add_argument("--scope", action="append", help="limitar ao alcance informado; pode ser usado mais de uma vez")
    source_search = sub.add_parser("source-search", help="localizar trechos nos documentos-fonte importados")
    source_search.add_argument("query")
    source_search.add_argument("--limit", type=int, default=6)
    source_search.add_argument("--scope", action="append", help="limitar aos projetos informados")
    source_replace = sub.add_parser("source-replace", help="substituir um documento-fonte após conferir sua versão")
    source_replace.add_argument("path", help="caminho completo, por exemplo sources/projeto/notas.md")
    source_replace.add_argument("--file", type=Path, required=True, help="arquivo Markdown novo")
    source_replace.add_argument("--expected-sha256", required=True, help="sha256 atual da fonte, obtido antes da edição")
    source_delete = sub.add_parser("source-delete", help="remover um documento-fonte dos arquivos atuais")
    source_delete.add_argument("path", help="caminho completo, por exemplo sources/projeto/notas.md")
    source_delete.add_argument("--expected-sha256", required=True, help="sha256 atual da fonte, obtido antes da remoção")
    semantic = sub.add_parser("semantic-search", help="buscar por significado com o serviço opcional configurado")
    semantic.add_argument("query")
    semantic.add_argument("--mode", choices=["all", "memory", "sources"], default="all")
    semantic.add_argument("--limit", type=int, default=5)
    semantic.add_argument("--scope", action="append", help="limitar aos projetos informados")
    sub.add_parser("semantic-rebuild", help="recriar o índice semântico local")
    sub.add_parser("semantic-compact", help="reduzir o espaço do índice semântico local sem recalcular vetores")
    context = sub.add_parser("context", help="preparar um resumo para passar a um assistente")
    context.add_argument("query")
    context.add_argument("--limit", type=int, default=5)
    context.add_argument("--budget-chars", type=int, default=5000, help="limite aproximado do contexto preparado")
    context.add_argument("--include-sources", action="store_true", help="inclui até dois trechos de documentos-fonte")
    context.add_argument("--workstream-id")
    context.add_argument("--scope", action="append", help="incluir apenas estes alcances; pode ser usado mais de uma vez")
    handoff = sub.add_parser("handoff", help="anotar onde o trabalho parou e o que vem depois")
    handoff.add_argument("--status", required=True, help="estado atual do trabalho, por exemplo in_progress, blocked ou completed")
    handoff.add_argument("--summary", required=True, help="resumo do que está acontecendo")
    handoff.add_argument("--blocker", action="append", default=[], help="problema que impede o avanço; pode ser usado mais de uma vez")
    handoff.add_argument("--question", action="append", default=[], help="pergunta ainda sem resposta; pode ser usada mais de uma vez")
    handoff.add_argument("--next", dest="next_actions", action="append", default=[], help="próxima ação; pode ser usada mais de uma vez")
    handoff.add_argument("--by", default="human", help="pessoa ou agente que registrou esta passagem")
    handoff.add_argument("--session-id", help="identificador da conversa relacionada")
    handoff.add_argument("--workstream-id", help="frente de trabalho relacionada")
    handoff.add_argument("--scope", default="default", help="alcance da passagem de trabalho")
    sub.add_parser("rebuild", help="recriar a busca a partir dos arquivos de memória")
    restore = sub.add_parser("restore", help="restaurar a memória a partir de um repositório Git")
    restore.add_argument("--from", dest="source", required=True, help="URL ou caminho do repositório de backup")
    restore.add_argument("--branch", default="main", help="branch do backup (padrão: main)")
    sync = sub.add_parser("sync", help="sincronizar as memórias com o backup Git configurado")
    sync.add_argument("--dry-run", action="store_true",
                      help="conferir o que aconteceria sem alterar arquivos ou Git")
    sync.add_argument("--json", action="store_true", help="mostrar o resultado em JSON para automações")
    workstream = sub.add_parser("workstream", help="organizar frentes de trabalho paralelas")
    workstream_sub = workstream.add_subparsers(dest="workstream_command", required=True)
    workstream_create = workstream_sub.add_parser("create", help="criar uma frente de trabalho")
    workstream_create.add_argument("--id", required=True, help="identificador curto, sem espaços")
    workstream_create.add_argument("--title", required=True, help="nome da frente de trabalho")
    workstream_create.add_argument("--by", default="human", help="quem registrou a frente")
    workstream_create.add_argument("--status", choices=["active", "completed", "archived"], default="active", help="estado da frente")
    workstream_sub.add_parser("list", help="mostrar as frentes de trabalho")
    history = sub.add_parser("history", help="revisar anotações extraídas de conversas antigas")
    history_sub = history.add_subparsers(dest="history_command", required=True)
    history_stage = history_sub.add_parser("stage", help="preparar anotações para revisão; elas ainda não entram na memória")
    history_stage.add_argument("--file", type=Path, required=True, help="arquivo JSON no formato omm-history-v1")
    history_show = history_sub.add_parser("show", help="ver as anotações preparadas para revisão")
    history_show.add_argument("import_id", help="identificador mostrado ao preparar a importação")
    history_sub.add_parser("list", help="mostrar importações preparadas e concluídas")
    history_accept = history_sub.add_parser("accept", help="adicionar à memória as anotações revisadas")
    history_accept.add_argument("import_id", help="identificador da importação revisada")
    history_accept.add_argument("--by", required=True, help="pessoa que revisou e aprovou as anotações")
    history_copilot = history_sub.add_parser("import-copilot", help="importar conversa do Copilot como documento pesquisável")
    history_copilot.add_argument("--file", type=Path, required=True, help="exportação JSON do GitHub Copilot")
    history_copilot.add_argument("--scope", required=True, help="escopo do projeto dono do histórico")
    history_copilot.add_argument("--title", default="Conversa importada do GitHub Copilot", help="título mostrado no histórico")
    history_claude = history_sub.add_parser("import-claude", help="importar texto visível de uma sessão Claude Code como documento pesquisável")
    history_claude.add_argument("--file", type=Path, required=True, help="arquivo local da sessão Claude Code (.jsonl)")
    history_claude.add_argument("--scope", required=True, help="projeto dono do histórico")
    history_claude.add_argument("--title", required=True, help="nome curto da conversa")
    history_claude.add_argument("--session-id", help="identificador da sessão, se for diferente do nome do arquivo")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    omm = OMM(args.root)
    if args.command == "init":
        omm.init()
        print(f"OMM preparado em {omm.root}")
    elif args.command == "doctor":
        checks = diagnose(omm)
        if args.json:
            print(json.dumps(checks, ensure_ascii=False, indent=2))
        else:
            icons = {"ok": "✓", "warning": "!", "error": "✗", "info": "i"}
            for check in checks:
                print(f"{icons.get(check['status'], '-')} {check['message']}")
        return 1 if any(check["status"] == "error" for check in checks) else 0
    elif args.command == "remember":
        record = MemoryRecord(kind=args.kind, title=args.title, content=args.content, source=args.source,
                              evidence=args.evidence, tags=args.tag, role=args.role,
                              created_by=args.created_by, confidence=args.confidence,
                              session_id=args.session_id, workstream_id=args.workstream_id, scope=args.scope)
        omm.remember(record)
        print(record.id)
    elif args.command == "search":
        for record in omm.search(args.query, args.limit, args.workstream_id, args.scope):
            print(json.dumps({"id": record.id, "kind": record.kind, "title": record.title,
                              "content": record.content, "source": record.source,
                              "evidence": record.evidence, "tags": record.tags,
                              "session_id": record.session_id, "workstream_id": record.workstream_id,
                              "scope": record.scope}, ensure_ascii=False))
    elif args.command == "source-search":
        for hit in omm.search_sources(args.query, args.limit, args.scope):
            print(json.dumps({"id": hit.id, "scope": hit.scope, "heading": hit.heading,
                              "content": hit.content, "source": hit.source,
                              "authority": "locator_only"}, ensure_ascii=False))
    elif args.command == "source-replace":
        try:
            result = omm.replace_source(args.path, args.file.read_text(encoding="utf-8"), args.expected_sha256)
        except (OSError, ValueError) as exc:
            print(f"Fonte não atualizada: {exc}", file=sys.stderr)
            return 2
        omm.rebuild()
        print(json.dumps({**result, "search_index": "rebuilt",
                          "git_history_note": "Versões anteriores podem permanecer em commits antigos."},
                         ensure_ascii=False))
    elif args.command == "source-delete":
        try:
            result = omm.delete_source(args.path, args.expected_sha256)
        except (OSError, ValueError) as exc:
            print(f"Fonte não removida: {exc}", file=sys.stderr)
            return 2
        omm.rebuild()
        print(json.dumps({**result, "search_index": "rebuilt",
                          "git_history_note": "Versões anteriores podem permanecer em commits antigos."},
                         ensure_ascii=False))
    elif args.command == "semantic-search":
        print(json.dumps(omm.semantic_search(args.query, args.mode, args.limit, args.scope),
                         ensure_ascii=False, indent=2))
    elif args.command == "semantic-rebuild":
        print(f"Índice semântico recriado: {omm.rebuild_semantic()} itens")
    elif args.command == "semantic-compact":
        try:
            result = omm.compact_semantic_index()
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            print(f"Índice semântico não compactado: {type(exc).__name__}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "context":
        state_scope = args.scope[-1] if args.scope else None
        sys.stdout.write(omm.context(args.query, args.limit, args.workstream_id, args.scope, state_scope,
                                     include_sources=args.include_sources, budget_chars=args.budget_chars))
    elif args.command == "handoff":
        omm.handoff(args.status, args.summary, args.blocker, args.question, args.next_actions, args.by,
                    args.session_id, args.workstream_id, args.scope)
        print("Passagem de trabalho registrada")
    elif args.command == "rebuild":
        record_count = omm.rebuild()
        print(f"Busca recriada: {record_count} anotações canônicas e "
              f"{omm.source_chunk_count()} trechos de documentos-fonte")
    elif args.command == "restore":
        try:
            count = restore_from_git(args.root, args.source, args.branch,
                                     os.getenv("OMM_GIT_BACKUP_USERNAME", ""),
                                     os.getenv("OMM_GIT_BACKUP_TOKEN", ""))
        except RestoreError as exc:
            print(f"Restauração interrompida: {exc}", file=sys.stderr)
            return 2
        print(f"Memória restaurada em {args.root}; {count} anotações carregadas e busca reconstruída.")
    elif args.command == "sync":
        dry_run = args.dry_run
        common_options = (
            args.root,
            os.getenv("OMM_GIT_BACKUP_REMOTE", "origin"),
            os.getenv("OMM_GIT_BACKUP_BRANCH", "main"),
            os.getenv("OMM_GIT_BACKUP_REPOSITORY_URL", "").strip(),
            os.getenv("OMM_GIT_BACKUP_USERNAME", "x-access-token"),
            os.getenv("OMM_GIT_BACKUP_TOKEN", ""),
        )
        try:
            if dry_run:
                result = preview_sync(*common_options)
                if args.json:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    state = "sem bloqueios conhecidos" if result["ok"] else "precisa de atenção"
                    relationship_labels = {
                        "up_to_date": "os commits local e remoto são iguais",
                        "remote_ahead": "o backup tem commits mais novos",
                        "local_ahead": "esta cópia tem commits mais novos",
                        "diverged": "as branches seguiram caminhos diferentes",
                        "remote_relation_unknown": "não foi possível comparar sem baixar arquivos",
                    }
                    print(f"Simulação concluída: {state}.")
                    print(f"Branch {result['branch']}: {relationship_labels.get(result['relationship'], result['relationship'])}.")
                    for path in result["local_changes"]["canonical_paths"]:
                        print(f"  dado da OMM alterado: {path}")
                    for path in result["local_changes"]["outside_paths"]:
                        print(f"  fora dos dados da OMM: {path}")
                    for reason in result["blocking_reasons"]:
                        print(f"  atenção: {reason}")
                    print("Nada foi salvo, mesclado ou enviado ao Git.")
                    for limitation in result["limitations"]:
                        print(f"  limite: {limitation}")
                return 0 if result["ok"] else 2

            report = sync_with_backup_report(
                *common_options,
                os.getenv("OMM_GIT_BACKUP_AUTHOR_NAME", "OMM Backup"),
                os.getenv("OMM_GIT_BACKUP_AUTHOR_EMAIL", "omm@localhost"),
            )
            indexed_records = omm.rebuild()
        except (SyncError, BackupError, OSError, ValueError) as exc:
            if args.json:
                print(json.dumps({"ok": False, "dry_run": dry_run, "error": str(exc)},
                                 ensure_ascii=False))
            else:
                print(f"Sincronização interrompida: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({**report, "dry_run": False, "search_index": "rebuilt",
                              "indexed_records": indexed_records,
                              "source_chunks": omm.source_chunk_count()}, ensure_ascii=False))
        else:
            print(report["message"])
    elif args.command == "workstream":
        if args.workstream_command == "create":
            print(json.dumps(omm.create_workstream(args.id, args.title, args.by, args.status), ensure_ascii=False))
        else:
            for item in omm.workstreams():
                print(json.dumps(item, ensure_ascii=False))
    elif args.command == "history":
        if args.history_command == "stage":
            staged = stage_history(omm.root, args.file.read_bytes())
            print(f"{len(staged['records'])} anotação(ões) preparada(s) para revisão: {staged['id']}")
        elif args.history_command == "show":
            print(json.dumps(show_history_import(omm.root, args.import_id), ensure_ascii=False, indent=2))
        elif args.history_command == "list":
            for item in list_history_imports(omm.root):
                print(json.dumps({"id": item["id"], "platform": item["platform"],
                                  "session_id": item["session_id"], "status": item["status"],
                                  "candidate_count": len(item["records"])}, ensure_ascii=False))
        elif args.history_command == "import-copilot":
            try:
                imported = import_copilot_chat(omm.root, args.file, args.scope, args.title)
            except (OSError, ValueError) as exc:
                print(f"Importação interrompida: {exc}", file=sys.stderr)
                return 2
            omm.rebuild()
            print(json.dumps(imported, ensure_ascii=False))
        elif args.history_command == "import-claude":
            try:
                imported = import_claude_session(omm.root, args.file, args.scope,
                                                  args.title, args.session_id)
            except (OSError, ValueError) as exc:
                print(f"Importação interrompida: {exc}", file=sys.stderr)
                return 2
            omm.rebuild()
            print(json.dumps(imported, ensure_ascii=False))
        else:
            accepted = accept_history(omm.root, args.import_id, args.by)
            omm.rebuild()
            print(f"{len(accepted)} anotação(ões) adicionada(s) à memória do projeto")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
