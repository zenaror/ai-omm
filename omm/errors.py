"""Safe execution errors; never serialize exception text or tool arguments."""
from __future__ import annotations

from functools import wraps
import json
import logging


class OMMError(ValueError):
    def __init__(self, code: str, message: str, recovery: str):
        super().__init__(message)
        self.code, self.message, self.recovery = code, message, recovery


def error_report(error: Exception, operation: str) -> dict:
    if isinstance(error, OMMError):
        code, message, recovery = error.code, error.message, error.recovery
        outcome = "not_applied"
    else:
        outcome = "unknown"
        if isinstance(error, (FileNotFoundError, KeyError)):
            code, message = "NOT_FOUND", "O recurso solicitado não foi encontrado."
            recovery = "Confira o identificador e a origem. Não substitua uma fonte ausente por um resumo antigo."
        elif isinstance(error, PermissionError):
            code, message = "PERMISSION_DENIED", "A operação não tem acesso ao recurso."
            recovery = "Confira as permissões com a administração; não tente outro caminho para contorná-las."
        elif isinstance(error, FileExistsError):
            code, message = "ALREADY_EXISTS", "Já existe um recurso nesse destino."
            recovery = "Leia o recurso atual; use a atualização protegida por SHA quando apropriado."
        elif isinstance(error, (ValueError, TypeError)):
            code, message = "INVALID_ARGUMENT", "Um argumento ou dado recebido não pôde ser validado."
            recovery = "Confira os campos e limites da ferramenta antes de corrigir a chamada."
        elif isinstance(error, TimeoutError):
            code, message = "TIMEOUT", "A operação não terminou no tempo disponível."
            recovery = "Confira o estado por leitura antes de repetir uma gravação."
        elif isinstance(error, OSError):
            code, message = "STORAGE_UNAVAILABLE", "Não foi possível acessar os arquivos necessários."
            recovery = "Preserve o checkpoint local e confira a instalação; não declare sincronização."
        else:
            code, message = "INTERNAL_ERROR", "A ferramenta encontrou uma falha interna."
            recovery = "Informe operação e código à administração, sem credenciais. Confira o estado antes de repetir."
    # Fixed guidance only: never include the rejected value or exception text.
    if not isinstance(error, OMMError):
        if code == "NOT_FOUND" and operation in {"read_source", "read_source_batch"}:
            recovery = "Use search_sources para localizar o caminho exato antes de ler novamente. Não repita o caminho ausente nem o substitua por um resumo."
        elif code == "INVALID_ARGUMENT" and operation == "read_source":
            recovery = "Peça até 80 linhas e 8000 caracteres. Para recuperar texto completo, use read_source_batch com o cursor devolvido e o SHA-256."
        elif code == "INVALID_ARGUMENT" and operation == "read_source_batch":
            recovery = "Envie de 1 a 8 requests: path, start_char >= 0 e max_chars de 1 a 32768; máximo 65536 caracteres no lote. Reutilize next_start_char e sha256 devolvidos."
        elif code in {"NOT_FOUND", "INVALID_ARGUMENT"} and operation in {"get_skill", "get_role"}:
            recovery = "Consulte list_skills ou list_roles e use o nome exato retornado antes de repetir a leitura."
        elif code == "INVALID_ARGUMENT" and operation == "read_artifact":
            recovery = "Consulte os metadados do arquivo e use seu SHA-256, offset em bytes e max_bytes permitido. Retome pelo cursor devolvido."
    return {"error": {"code": code, "operation": operation, "message": message,
                      "recovery": recovery, "retryable": False, "write_outcome": outcome}}


def guard_tool(function, tool_error_type):
    """Use the SDK's anticipated-error path, preserving the original signature."""
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:
            report = error_report(error, function.__name__)
            logging.getLogger(__name__).warning(
                "OMM operation=%s code=%s exception_type=%s",
                function.__name__, report["error"]["code"], type(error).__name__)
            raise tool_error_type(json.dumps(report, ensure_ascii=False)) from None
    return guarded
