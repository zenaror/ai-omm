# Ferramentas da OMM, explicadas de forma simples

Este guia explica cada ferramenta que o assistente pode chamar pela conexão MCP. Pense na OMM como um caderno compartilhado com uma biblioteca: **memórias** são anotações escolhidas, **fontes** são documentos completos, **skills** são modos de trabalhar e **papéis** são responsabilidades.

Você pode pedir em português: “Procure na OMM onde paramos no meu projeto”. O assistente escolhe a ferramenta e envia os parâmetros. Os blocos JSON abaixo mostram esses parâmetros; **não são comandos para colar no terminal**. Troque `meu-projeto`, caminhos, IDs e hashes pelos valores reais.

**Escopo** é a gaveta do projeto. `global` guarda o que vale para vários projetos. Em buscas, `include_global=false` consulta só a gaveta escolhida; `true` inclui a global. **SHA-256** é a impressão digital do conteúdo: muda quando o arquivo muda. Nunca invente um hash nem use apenas o começo dele numa alteração.

## Qual ferramenta escolho?

| Quero… | Começo por… |
|---|---|
| Retomar o trabalho | `context` |
| Encontrar uma anotação | `search` → `get_memory` |
| Conferir um documento | `search_sources` → `read_source` |
| Procurar pelo assunto | `semantic_search` → `semantic_index_status` se precisar |
| Sugerir algo para guardar | `propose_memory` |
| Guardar diretamente, com pedido da pessoa | `remember` |
| Saber como trabalhar | `list_skills` → `get_skill` |
| Saber quem faz o quê | `get_agent_topology`, `list_roles` → `get_role` |
| Deixar o próximo passo | `handoff` |
| Organizar frentes paralelas | `list_workstreams` → `get_workstream` |
| Conferir uso e tamanho das chamadas | `usage_report` |
| Investigar problema na instalação | `diagnose_setup` |

As seções seguintes explicam as ferramentas MCP da aplicação. As ferramentas disponíveis numa conexão podem variar com a versão instalada; depois de atualizar, reconecte o MCP.

## Medir o uso

### `usage_report`

Mostra quanto texto passa pelas ferramentas MCP e quantas chamadas foram feitas.

```json
{"days": 7, "scope": "meu-projeto"}
```

O escopo é opcional. O relatório soma o período em UTC e mostra os 50 maiores grupos por ferramenta/projeto, incluindo falhas e tempos. Os totais incluem os grupos que ficaram fora da lista; `truncated=true` avisa o corte. Consulte um projeto específico para reduzir a resposta. A consulta ao relatório não conta a si mesma.

`error_rows` mostra até 50 grupos de falhas por ferramenta, projeto e categoria,
como `VERSION_CONFLICT`. `errors_truncated` avisa se há mais grupos.
`classified_failures` conta as falhas com categoria; `unclassified_failures`
inclui as antigas, quando essa informação ainda não era coletada. Não deduza
a causa das falhas antigas a partir das novas. Erros de autenticação ou de
validação do protocolo, que não chegaram à execução da ferramenta, ficam fora.

**Limites:** são caracteres JSON e tempos internos, não tokens cobrados ou economia provada. Não inclui chamadas ao painel ou à CLI, tempo de rede, cache, descrições das ferramentas ou custos externos dos subagentes. Não guarda perguntas, respostas, senhas, mensagens de erro ou hashes de consultas. Não distingue clientes nem mede repetições. Os nomes de escopo são guardados; não coloque dados pessoais neles. Retém 30 dias por padrão em `.omm/usage.sqlite3`, fora do backup Git. A coleta começa nesta versão, sem histórico retroativo. Veja as [opções de coleta](USAR_OMM_NOS_AGENTES.md#medição-contínua-das-chamadas).

## Consultar o que já existe

### `context`

O padrão MCP é 2.500 caracteres. Use `budget_chars` para ampliar quando necessário; o resumo sinaliza quando foi reduzido.

Prepare um bilhete curto para retomar o projeto. Reúne memórias relevantes e o estado do trabalho.

**O que acontece e o que conferir:** Consulta; não salva memórias. Por padrão não inclui trechos de documentos. Use um orçamento pequeno de texto e abra detalhes depois.

**Exemplo de parâmetros:**

```json
{
  "query": "onde paramos e qual é o próximo passo",
  "scope": "meu-projeto",
  "include_global": false,
  "budget_chars": 3000
}
```

### `search`

Procure palavras nas anotações escolhidas para a memória. Mostra resumos e IDs para abrir depois.

**O que acontece e o que conferir:** Consulta. Retorna no máximo 10 anotações; registros substituídos ficam fora da busca comum.

**Exemplo de parâmetros:**

```json
{
  "query": "comparação binária",
  "scope": "meu-projeto",
  "include_global": false,
  "limit": 5
}
```

### `get_memory`

Abra uma anotação inteira que encontrou na busca, ou leia somente o trecho necessário.

**O que acontece e o que conferir:** Consulta. Use o ID completo retornado por search; um prefixo curto não basta. Também permite abrir registros antigos pelo ID.

**Exemplo de parâmetros:**

```json
{
  "record_id": "ID_COMPLETO_DA_ANOTACAO"
}
```

Para uma anotação longa, use `max_chars` (por exemplo, 2000). O resultado informa `total_chars`, `truncated`, `next_start_char` e o `content_sha256` do corpo inteiro. Continue com `start_char` igual a `next_start_char`; os offsets são caracteres Unicode, começando em zero. Se o hash mudar entre trechos, reinicie a leitura da nova versão. `next_start_char: null` indica o fim. Sem esses parâmetros, a chamada continua devolvendo a anotação inteira como antes. Os metadados e evidências permanecem completos.

```json
{
  "record_id": "ID_COMPLETO_DA_ANOTACAO",
  "start_char": 0,
  "max_chars": 2000
}
```

### `get_memory_digest`

Confira a identidade e a versão de uma anotação sem mostrar o corpo dela.

**O que acontece e o que conferir:** Consulta. Retorna metadados e a impressão digital do corpo, chamada SHA-256. Útil antes de uma limpeza.

**Exemplo de parâmetros:**

```json
{
  "record_id": "ID_COMPLETO_DA_ANOTACAO"
}
```

### `search_sources`

Procure palavras nos documentos guardados na OMM. É como localizar a página certa de um livro.

**O que acontece e o que conferir:** Consulta. Retorna até 3 trechos curtos, mesmo se pedir mais. O trecho é uma pista; confira o documento com read_source.

**Exemplo de parâmetros:**

```json
{
  "query": "comparação com a ROM original",
  "scope": "meu-projeto",
  "include_global": false,
  "limit": 3
}
```

### `read_source`

Leia algumas linhas de um documento e obtenha a impressão digital do arquivo inteiro.

**O que acontece e o que conferir:** Consulta. O path é o caminho da fonte; start_line e end_line escolhem as linhas. O hash é do arquivo inteiro, mesmo quando lê só um trecho.

**Exemplo de parâmetros:**

```json
{
  "path": "sources/meu-projeto/docs/regras.md",
  "start_line": 1,
  "end_line": 40
}
```

### `semantic_search`

Procure pelo assunto, mesmo usando palavras diferentes das que foram guardadas.

**O que acontece e o que conferir:** Consulta que pode preparar um cache de busca. Precisa da busca semântica habilitada. Se vier status=building, acompanhe a preparação e use a busca comum enquanto espera. Confira a origem dos resultados.

**Exemplo de parâmetros:**

```json
{
  "query": "como manter a reconstrução fiel ao original",
  "scope": "meu-projeto",
  "include_global": false,
  "mode": "all",
  "limit": 5
}
```

### `semantic_index_status`

Veja se a busca por assunto já está pronta.

**O que acontece e o que conferir:** Consulta. Se estiver building, ainda está preparando; quando estiver ready, repita semantic_search. Evite consultar repetidamente sem dar tempo para o trabalho avançar.

**Exemplo de parâmetros:**

```json
{
  "scope": "meu-projeto",
  "include_global": false
}
```

## Guardar e corrigir anotações

### `propose_memory`

Deixe uma nova anotação na caixa de sugestões para a pessoa aprovar ou recusar.

**O que acontece e o que conferir:** Grava uma sugestão; ainda não a coloca na memória aprovada. Informa possíveis anotações semelhantes. É o caminho normal para sugerir conhecimento novo.

**Exemplo de parâmetros:**

```json
{
  "kind": "decision",
  "title": "Preservar a ROM original",
  "content": "A ROM original é imutável; alterações usam cópias.",
  "source": "docs/regras.md:12",
  "scope": "meu-projeto"
}
```

### `list_memory_proposals`

Veja quais sugestões ainda aguardam revisão.

**O que acontece e o que conferir:** Consulta. Mostra até 20 sugestões, com texto curto e possíveis semelhantes. A aprovação ou recusa acontece no painel; esta ferramenta não aprova.

**Exemplo de parâmetros:**

```json
{
  "limit": 10
}
```

### `remember`

Guarde uma anotação diretamente, sem passar pela caixa de sugestões.

**O que acontece e o que conferir:** Grava uma memória. Use quando a pessoa pedir para salvar diretamente. Não edita uma anotação existente; para uma correção, crie o sucessor e marque a anterior como substituída.

**Exemplo de parâmetros:**

```json
{
  "kind": "fact",
  "title": "Build de referência conferido",
  "content": "A saída foi comparada byte a byte com a referência identificada no relatório.",
  "source": "docs/relatorio.md:20",
  "scope": "meu-projeto",
  "evidence": [
    "docs/relatorio.md:20-25"
  ]
}
```

### `set_memory_status`

Troque a etiqueta de uma anotação: atual, substituída, retirada ou ainda não verificada.

**O que acontece e o que conferir:** Grava o status, sem editar ou apagar o corpo. active = atual; superseded = substituída; retracted = retirada; unverified = não verificada. Crie e confira o sucessor antes de substituir a antiga.

**Exemplo de parâmetros:**

```json
{
  "record_id": "ID_COMPLETO_DA_ANOTACAO_ANTIGA",
  "status": "superseded"
}
```

### `redact_memory_content`

Troque um identificador de conta REON por um marcador em uma anotação antiga.

**O que acontece e o que conferir:** Altera só o corpo, preservando ID e metadados. Aceita apenas registros não ativos e a regra reon_gid (g seguido de nove dígitos). Obtenha o hash com get_memory_digest. Não é um editor geral de memórias e não apaga versões antigas do Git.

**Exemplo de parâmetros:**

```json
{
  "record_id": "ID_COMPLETO_DA_ANOTACAO_ANTIGA",
  "expected_sha256": "HASH_ATUAL_DO_CORPO",
  "redaction_rule": "reon_gid"
}
```

## Guardar e manter documentos

### `import_source`

Coloque um documento Markdown completo na biblioteca da OMM.

**O que acontece e o que conferir:** Cria sources/<escopo>/<caminho>. Recebe o texto inteiro, não só um link. Aceita até 20 MiB, verifica credenciais e não sobrescreve conteúdo diferente. Se já for idêntico, informa already_present.

**Exemplo de parâmetros:**

```json
{
  "scope": "meu-projeto",
  "relative_path": "docs/regras.md",
  "content": "# Regras\n\nA referência original deve ser preservada.\n"
}
```

### `patch_source`

Corrige um pequeno trecho sem enviar o documento inteiro novamente.

**Como usar:** procure a fonte, leia o trecho com `read_source` e copie o SHA-256 devolvido. Informe o texto antigo exato e o novo. Cada texto antigo precisa aparecer uma única vez; inclua mais contexto se houver repetição. Todas as trocas usam a mesma versão original e são aplicadas juntas. Se a fonte mudou, nenhum trecho é salvo.

```json
{
  "path": "sources/meu-projeto/ROADMAP.md",
  "expected_sha256": "SHA_INTEGRAL_ATUAL",
  "edits": [{"old_text": "- Etapa 2: pendente", "new_text": "- Etapa 2: concluída; evidência no commit abc123"}]
}
```

Confira o trecho novamente com `read_source`. São permitidas até 50 trocas sem sobreposição, somando até 32.000 caracteres nos textos antigos e novos. O resultado inteiro passa pela proteção contra credenciais. Para trocar um documento inteiro, use `replace_source`. Para retirar segredos sem enviá-los novamente, use `redact_source_spans`. Versões antigas podem continuar no Git.

### `replace_source`

Corrija um documento que já está na biblioteca, no mesmo caminho.

**O que acontece e o que conferir:** Substitui o arquivo atual. Leia primeiro e envie seu SHA-256 para evitar apagar uma edição recente de outra sessão. Envie o conteúdo completo revisado; confira depois. A versão antiga pode continuar no histórico Git.

**Exemplo de parâmetros:**

```json
{
  "path": "sources/meu-projeto/docs/regras.md",
  "content": "# Regras\n\nUse uma cópia para qualquer alteração.\n",
  "expected_sha256": "HASH_ATUAL_DA_FONTE"
}
```

### `redact_source_spans`

Cubra trechos específicos com marcadores, sem reenviar os valores que quer esconder.

**O que acontece e o que conferir:** Altera o documento. Informe o hash atual e a posição exata de cada trecho. Linhas e colunas começam em 1, e ambas as colunas são inclusivas. O tamanho é contado em caracteres. O exemplo cobre 5 caracteres da linha 12; só use se essas posições tiverem sido conferidas. Não limpa o histórico Git.

**Exemplo de parâmetros:**

```json
{
  "path": "sources/meu-projeto/docs/relatorio.md",
  "expected_sha256": "HASH_ATUAL_DA_FONTE",
  "spans": [
    {
      "line": 12,
      "start_column": 9,
      "end_column": 13,
      "expected_length": 5
    }
  ]
}
```

### `delete_source`

Retire um documento dos arquivos atuais da OMM.

**O que acontece e o que conferir:** Remove a fonte e sua presença na busca atual. Exige o hash de read_source. Não remove cópias já guardadas no histórico Git. Use apenas quando a remoção estiver autorizada.

**Exemplo de parâmetros:**

```json
{
  "path": "sources/meu-projeto/docs/arquivo-obsoleto.md",
  "expected_sha256": "HASH_ATUAL_DA_FONTE"
}
```

## Skills, papéis e mapa de ajudantes

### `list_skills`

Veja o cardápio de instruções disponíveis para o projeto, sem abrir todas.

**O que acontece e o que conferir:** Consulta. O nome correto é list_skills, no plural. Com scope, lista as skills daquele projeto e as bases herdadas. Sem scope, lista todas. Mostra descrição, hash, pais em inherits e, ao filtrar por escopo, quem herda a base em inherited_by.

**Exemplo de parâmetros:**

```json
{
  "scope": "mobile-trainer"
}
```

### `get_skill`

Abra as instruções de uma skill, já com as bases que ela herda.

**O que acontece e o que conferir:** Consulta. A ordem é da base geral à especialização; uma base compartilhada aparece uma vez. Para editar só o arquivo da skill, peça resolve_inheritance=false; não salve o texto combinado como se fosse o arquivo original.

**Exemplo de parâmetros:**

```json
{
  "name": "mobile-trainer-reverse-engineering"
}
```

### `save_skill`

Crie ou atualize uma skill reutilizável.

**O que acontece e o que conferir:** Grava nos dados da OMM. Exige cabeçalho com name igual ao nome pedido e description; limite de 1 MiB. Para atualizar, abra o arquivo isolado com get_skill e copie o hash atual de list_skills. Pais inexistentes, ciclos de herança e credenciais detectadas são recusados. Não inicia ajudantes.

**Exemplo de parâmetros:**

```json
{
  "name": "meu-jogo",
  "content": "---\nname: meu-jogo\ndescription: Regras específicas para reconstruir este jogo.\nmetadata:\n  scope: project:meu-jogo\n  inherits:\n    - gameboy-gbc-reverse-engineering\n---\n\n# Meu jogo\n\nPreserve a referência original.\n"
}
```

### `list_roles`

Veja os papéis disponíveis, como coordenador, planejador e executor.

**O que acontece e o que conferir:** Consulta. Mostra nomes, resumos e hashes. Não chama nenhum ajudante.

**Exemplo de parâmetros:**

```json
{}
```

### `get_role`

Abra as responsabilidades de um papel escolhido.

**O que acontece e o que conferir:** Consulta. Um papel diz quem faz o quê; uma skill diz como trabalhar em um assunto. Não cria subagentes.

**Exemplo de parâmetros:**

```json
{
  "name": "coordinator"
}
```

### `save_role`

Crie ou atualize as responsabilidades de um papel.

**O que acontece e o que conferir:** Grava em memory/roles/. Para atualizar, abra get_role e copie o hash de list_roles como expected_sha256. Limite de 1 MiB. Não altera o mapa nem inicia sessões.

**Exemplo de parâmetros:**

```json
{
  "name": "meu-projeto/revisor",
  "content": "# Revisor\n\nConfira a evidência e relate dúvidas ao coordenador.\n"
}
```

### `get_agent_topology`

Abra o mapa que diz quem coordena, quais ajudantes se aplicam e quais conversas são históricas.

**O que acontece e o que conferir:** Consulta. Com scope, retorna o perfil próprio quando existe (`profile_found=true`). Sem perfil próprio, já devolve o padrão global, com `profile_found=false` e `fallback=true`. Não cria perfil nem exige outra consulta. Um mapa inválido continua sendo erro. O mapa não inicia ajudantes: isso depende do aplicativo do assistente.

**Exemplo de parâmetros:**

```json
{}
```

### `add_historical_source`

Ligue ao perfil do projeto uma conversa que já foi importada.

**O que acontece e o que conferir:** Altera o mapa; não importa a conversa nem cria agente ativo. O projeto precisa ter perfil. O hash esperado é o do mapa, retornado por get_agent_topology, e não o hash da transcrição.

**Exemplo de parâmetros:**

```json
{
  "scope": "meu-projeto",
  "name": "Conversa antiga de investigação",
  "platform": "Claude Code",
  "session_id": "ID_DA_SESSAO",
  "path": "sources/meu-projeto/historical-chats/conversa.md",
  "purpose": "Histórico para retomar decisões; não é agente ativo.",
  "expected_sha256": "HASH_ATUAL_DO_MAPA"
}
```

## Continuidade e organização dos projetos

Confira [Continuidade e diagnóstico](CONTINUIDADE.md) para comparar revisões,
entender códigos de erro e preservar um checkpoint quando o MCP faltar.
O SHA do handoff e o HEAD do Git são identidades diferentes.

### `get_handoff`

Além de estado e SHA, devolve `provenance` e `freshness`. Os argumentos opcionais
`observed_repository` e `observed_revision` comparam uma observação do cliente
com os campos registrados; não consultam o Git remoto nem comprovam testes.
Sem informação suficiente, o resultado é `not_verified`, inclusive para
checkpoints antigos sem revisão estruturada. Data antiga não significa divergência.

Abre o bilhete completo do projeto ou da frente de trabalho e devolve seu SHA-256. Use antes de gravar outro bilhete. `context` é resumido: pode não mostrar todas as pendências necessárias à mesclagem.

```json
{"scope": "meu-projeto", "workstream_id": "frente-existente"}
```

`workstream_id` é opcional. Quando informado, deve ser uma frente já criada. Use exatamente o mesmo escopo e a mesma frente ao gravar.

### `handoff`

Deixe o bilhete de passagem: onde parou, bloqueios, perguntas e próximos passos.

**O que acontece e o que conferir:** Grava uma passagem de trabalho. O novo handoff passa a representar o estado atual daquele escopo ou frente: leia o anterior e preserve o que ainda vale. As versões anteriores ficam registradas. Descobertas duradouras também merecem anotação própria.

**Proteção contra outra sessão:** leia `get_handoff` e acrescente `expected_sha256` com o hash recebido. Se outra sessão atualizou o estado, a OMM recusa a gravação. Leia novamente, mescle e tente com o novo hash. Chamadas antigas sem hash continuam aceitas por compatibilidade, mas não têm essa proteção.

`handoff` também aceita `repository`, `branch` e `git_revision` opcionais.
Informe um identificador simples do repositório e HEAD completo, sem URL ou
credencial. Esses campos não são inferidos do resumo nem certificados pelo servidor.

**Exemplo de parâmetros:**

```json
{
  "scope": "meu-projeto",
  "status": "waiting_operator",
  "summary": "Reconstrução conferida; falta a rodada de hardware.",
  "blockers": [
    "Aguardando observações do operador."
  ],
  "questions": [
    "O áudio foi ouvido sem cortes?"
  ],
  "next_actions": [
    "Registrar as observações e então analisar os logs."
  ]
}
```

### `status`

Para localizar uma frente sem carregar o estado geral, prefira `list_workstreams`.

Veja as contagens da OMM e as frentes de trabalho registradas.

**O que acontece e o que conferir:** Consulta. Ajuda a localizar estado e frentes, mas não prova que uma tarefa terminou nem que foi enviada ao backup remoto.

**Exemplo de parâmetros:**

```json
{}
```

### `list_workstreams` e `get_workstream`

Uma frente organiza trabalho paralelo. A lista usa páginas de 20 itens por padrão
(até 100) e pode filtrar por estado. IDs são únicos em toda a OMM.

```json
{"status": "active", "offset": 0, "limit": 20}
```

Se vier `next_offset`, use esse número na consulta seguinte. Para abrir uma
frente, chame `get_workstream` com `{"workstream_id":"meu-projeto-audio"}`.
O resultado inclui o SHA atual. Uma tarefa única pode usar o handoff central
sem cadastrar frente.

### `create_workstream`

Cadastre uma frente nova apenas quando a tarefa exigir e estiver autorizada.
Confira a lista antes para evitar duplicatas. Use um ID com nome de projeto.

```json
{"workstream_id":"meu-projeto-audio","title":"Áudio","actor":"agente","status":"active"}
```

Isso não cria agentes, metas nativas ou handoffs, nem concede permissões.

### `update_workstream`

Mude o nome ou o estado usando o SHA de `get_workstream`.

```json
{"workstream_id":"meu-projeto-audio","status":"paused","actor":"agente","expected_sha256":"HASH_COMPLETO_DA_LEITURA"}
```

Substitua o hash pelo valor real. Os estados são `active`, `paused`, `completed`
e `archived`. A ferramenta preserva as revisões anteriores e não muda handoffs.
Em conflito, releia e mescle. Para concluir, registre antes no handoff as provas
de que os critérios foram atendidos. O estado sozinho não prova conclusão.

### `merge_scope`

Junte duas gavetas de projeto que representam o mesmo trabalho.

**O que acontece e o que conferir:** Primeiro simula com dry_run=true. Revise o plano, as contagens e colisões. Para executar, repita com dry_run=false e expected_plan_sha256 igual ao hash da simulação atual. Altera dados; não sincroniza o backup.

**Exemplo de parâmetros:**

```json
{
  "source_scope": "nome-antigo",
  "target_scope": "nome-canonico",
  "dry_run": true
}
```

## Diagnóstico e busca

### `diagnose_setup`

Confira se a instalação, os arquivos, a busca e o backup local parecem prontos.

**O que acontece e o que conferir:** Consulta. Não corrige, não sincroniza e não confirma sozinho o conteúdo do remoto. Um índice semântico desatualizado não impede usar a busca por palavras.

**Exemplo de parâmetros:**

```json
{}
```

### `performance_report`

Meça quanto demoram as buscas, o resumo e o painel nesta instalação.

**O que acontece e o que conferir:** Executa medições sem devolver o conteúdo das memórias. Use quando solicitado; não é monitoramento contínuo. Os tempos são internos ao servidor, sem o tempo da rede. Pode consultar o serviço semântico se estiver habilitado e pronto.

**Exemplo de parâmetros:**

```json
{
  "repetitions": 30
}
```

### `rebuild_index`

Refaça o catálogo de busca a partir das memórias e documentos atuais.

**O que acontece e o que conferir:** Recria dados de busca por palavras, sem alterar os documentos canônicos. Não substitui a preparação do índice semântico e não faz backup. Edições normais já atualizam a busca; use para manutenção quando houver necessidade.

**Exemplo de parâmetros:**

```json
{}
```

## Três exemplos de uso completo

**Retomar um projeto:** peça `context` com o escopo certo. Se o resumo citar algo importante, procure com `search` e abra a anotação com `get_memory`. Para conferir a prova no documento, use `search_sources` e `read_source`. Abra só as skills e papéis necessários.

**Corrigir uma descoberta:** procure a versão atual. Sugira o texto correto com `propose_memory`, citando a evidência. Quando o sucessor estiver aprovado e conferido, use `set_memory_status` para marcar o antigo como `superseded`. Se houver autorização para guardar diretamente, use `remember` para o sucessor. Ao terminar, mescle o estado no `handoff`.

**Atualizar uma skill com herança:** leia o arquivo com `get_skill` e `resolve_inheritance=false`; obtenha o hash atual com `list_skills`. Edite o texto isolado e envie `save_skill` com `expected_sha256`. Depois, abra `get_skill` normalmente para conferir a cadeia completa. Para Mobile Trainer, a resposta deve trazer **disassembly geral → Game Boy/GBC → Mobile Trainer**.

## Limites que evitam confusão

- **Consultar não é comprovar:** resultados de busca são pistas. Confira a fonte e a revisão antes de tratar uma afirmação como atual.
- **Guardar não é fazer backup:** gravações MCP chegam aos dados da OMM. O envio ao Git segue o mecanismo de sincronização ou o backup agendado configurado na instalação. Não declare envio confirmado sem conferir o resultado.
- **Não existe ferramenta MCP de sync nesta versão.** A sincronização é feita pelo painel ou pelo terminal; veja o [guia de backup](USAR_MCP.md#sincronizar-pelo-terminal-ou-por-automação).
- **Remover do presente não apaga o passado:** `replace_source`, `delete_source` e as redações não limpam commits antigos do backup Git.
- **Marcar como substituído não redige o texto:** `set_memory_status` muda a etiqueta. O corpo antigo ainda pode ser aberto pelo ID.
- **Não existe editor geral de memórias no MCP:** registre um sucessor. `redact_memory_content` é uma exceção limitada aos IDs REON em registros não ativos.
- **O mapa não é uma chamada de ajudante:** skills, papéis e topologia só fornecem instruções. O aplicativo precisa ter sua própria função de subagentes.
- **O handoff não é uma anotação adicional ao estado:** mescle o anterior antes de gravar. Guardar só a novidade pode retirar bloqueios ou próximos passos que ainda valem.
- **Caches não são a memória:** buscas podem preparar ou atualizar índices; esses dados podem ser recriados. Fontes e memórias continuam sendo a referência.
- **O filtro de credenciais não garante ausência de dados pessoais:** revise o conteúdo antes de importar ou salvar. Nunca use credenciais reais nos exemplos.

## Campos úteis para anotações

`kind` diz o tipo: `fact` (fato), `observation` (observação), `hypothesis` (hipótese), `unknown` (desconhecido), `decision` (decisão), `conclusion` (conclusão), `constraint` (restrição) ou `contract` (contrato). Escolha o tipo que corresponde à evidência.

`source` diz de onde veio; `evidence` lista referências; `tags` facilita agrupar; `confidence` explica a confiança. `session_id` identifica a conversa e `workstream_id` liga a uma frente de trabalho já existente. Esses campos não substituem provas e não iniciam tarefas.

Para instalar a conexão, veja [Ligar um assistente à OMM](USAR_MCP.md). Para orientar o uso no projeto, veja [Como fazer um agente usar a OMM](USAR_OMM_NOS_AGENTES.md). A referência dos nomes e parâmetros está em [`omm/mcp_server.py`](../omm/mcp_server.py).
