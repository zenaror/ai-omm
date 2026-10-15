# Retomar o trabalho e conferir o checkpoint

Um checkpoint é uma fotografia do trabalho: o que foi feito, o que falta e
onde estão as provas. Ele não garante que o código atual seja o mesmo, que os
testes sejam suficientes ou que o backup remoto já tenha recebido os dados.

## Quando a OMM está disponível

1. Leia o `AGENTS.md` e identifique o projeto e a frente, se houver. Não invente
   uma frente: ela precisa existir na OMM. `status` mostra as cadastradas;
   o cadastro administrativo continua pela CLI `workstream create`.
2. Consulte `context` seletivamente e abra o handoff completo quando necessário.
   Confira no checkout real branch, HEAD, alterações locais e fontes canônicas.
3. Antes de gravar, leia `get_handoff` no mesmo escopo e frente. Preserve o que
   ainda vale, acrescente resultados e registre hipóteses e limitações como tais.
4. Grave com `expected_sha256` e confira o resultado por leitura posterior.
   Se houver conflito, leia novamente e mescle. Não remova o hash para forçar.

**Existem dois hashes diferentes:** o SHA do handoff protege uma atualização
da memória; o HEAD do Git identifica um commit do projeto. Não compare um ao
outro nem use o HEAD como `expected_sha256`.

### Comparar revisões explicitamente

`handoff` aceita os campos opcionais `repository`, `branch` e `git_revision`.
Use um identificador simples, como `equipe/projeto`, sem URL, token ou senha.
A revisão deve ser o hash completo retornado por `git rev-parse HEAD`.
Clientes antigos continuam funcionando. Sem esses campos, a OMM não tenta
adivinhar a revisão a partir do texto do resumo.

Em `get_handoff`, informe `observed_repository` e `observed_revision` para
comparar o checkout que você realmente conferiu com o registro:

```json
{
  "scope": "meu-projeto",
  "observed_repository": "equipe/projeto",
  "observed_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

O hash acima é só exemplo; substitua pelo HEAD real. A resposta acrescenta
`provenance` (origem e metadados registrados) e `freshness` (conferência).
Esses dados de leitura não alteram o SHA do estado:

| Estado | O que significa |
| --- | --- |
| `not_verified` | Faltam uma observação ou metadados compatíveis para comparar. |
| `matches` | Repositório e revisão informados coincidem com os registrados. |
| `diverged` | A revisão informada difere da registrada; confira antes de mesclar. |
| `no_checkpoint` | Não há checkpoint para a seleção. |

A comparação usa valores fornecidos pelo cliente; o servidor não abre o Git
do computador dele. `matches` não verifica arquivos sem commit, testes ou
publicação remota. Data antiga, sozinha, não prova obsolescência.
`selection_warning` avisa quando foi selecionado estado de outra frente;
confira a seleção antes de gravar.

Pela CLI, leia com `omm --root /data handoff-show --scope meu-projeto`.
Na gravação `handoff`, use `--expected-sha256`, `--repository`, `--branch` e
`--git-revision` quando apropriado. `/data` é a pasta de dados do servidor;
troque-a pela pasta correta da instalação. Clientes podem usar somente o MCP,
sem instalar a CLI no computador de cada usuário.

## Quando a conexão falta

Não afirme que consultou ou salvou a OMM. Separe servidor inacessível,
credencial recusada e ferramentas ausentes no cliente: uma conexão configurada
não demonstra que as ferramentas chegaram à sessão. Não registre credenciais
no checkpoint nem nas mensagens de diagnóstico.

Use o documento de continuidade já autorizado pelo projeto, por exemplo
`docs/current-handoff.md`. Se ele não existir, preserve um checkpoint no local
durável permitido pela tarefa. Não crie outra instalação OMM nem uma cópia
concorrente de toda a memória. Não use somente `/tmp` para evidências únicas.

Registre as informações já adotadas pelo projeto: objetivo e critérios,
escopo/frente existente, repositório/branch/HEAD, alterações locais, decisões e
autorizações com origem, evidências duráveis, comandos e resultados efetivos,
limitações, bloqueios e próximo passo seguro. Marque **pendente de mesclagem
com a OMM** e registre o último SHA conhecido, se houver. Um trabalho parcial
continua parcial; a desconexão não amplia permissões.

Quando a conexão voltar, confira Git e checkpoint local novamente. Leia o
handoff canônico atual e mescle com `expected_sha256` recém-obtido, preservando
avanços de outras sessões. Importe evidências úteis pelos mecanismos existentes
e confira a leitura posterior. Só então retire o aviso de mesclagem pendente.
Persistência na OMM não é confirmação de backup Git remoto.

## Entender os erros

Falhas durante a execução das ferramentas usam o caminho de erro do SDK MCP,
com `isError` e texto JSON contendo `error.code`, `message`, `recovery`,
`operation`, `retryable` e `write_outcome`. O cliente pode acrescentar um prefixo
ao texto. Resultados bem-sucedidos permanecem compatíveis.

| Código | Como proceder |
| --- | --- |
| `VERSION_CONFLICT` | Releia e mescle usando o SHA atual. A chamada recusada não gravou. |
| `UNKNOWN_WORKSTREAM` | Consulte `status` e peça o cadastro; não invente outra frente. |
| `PROFILE_NOT_FOUND` | Consulte a topologia global e as instruções do projeto. |
| `NOT_FOUND` | Confira o identificador e a origem do recurso. |
| `INVALID_ARGUMENT` | Confira campos, limites e tipos antes de corrigir a chamada. |
| `ALREADY_EXISTS` | Leia o recurso existente antes de escolher uma atualização. |
| `PERMISSION_DENIED` | Confira a permissão; não contorne a restrição. |
| `TIMEOUT`, `STORAGE_UNAVAILABLE`, `INTERNAL_ERROR` | Preserve o estado e informe o diagnóstico à administração. |

Sem garantia sobre o ponto da falha, `write_outcome` fica `unknown`. Confira o
estado antes de repetir: erro não garante que nada tenha sido persistido.
Não há repetição automática. A validação de protocolo/argumentos pelo SDK e a
autenticação HTTP continuam usando seus próprios erros; `401` não significa
que o servidor está desligado.

## Fontes, datas e backups

`get_memory` indica origem, status e hash do conteúdo. `not_current` identifica
registros substituídos ou retraídos; `not_verified` não certifica uma anotação
ativa como verdade atual. `read_source` indica hash integral e data do arquivo;
essa data não identifica a revisão do projeto nem a data de uma descoberta.
Confira trechos e procedência conforme a decisão exigir.

Origem canônica significa arquivos da pasta de dados consultada, não prova de
que o remoto Git está atualizado. Uma instalação restaurada de backup histórico
precisa ser identificada como tal pelo administrador/cliente; não há detecção
automática da história de um diretório copiado. `diagnose_setup` verifica o
backup local, não confirma envio ao remoto. Consulte a política da instalação
antes de sincronizar.
