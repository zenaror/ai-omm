# Como conferir se uma melhoria ajudou

Uma OMM melhor precisa encontrar a informação certa, responder em tempo útil e
enviar menos texto desnecessário. Meça essas três coisas separadamente.
Uma redução de caracteres ainda não é uma medição de tokens cobrados.

## Conferir a recuperação de conhecimento

Faça uma lista privada de perguntas que os projetos realmente precisam responder.
Para cada pergunta, confira o registro ou documento correto antes de executar a
avaliação. Inclua decisões importantes, evidências, regras substituídas, dúvidas
sem resposta e perguntas com palavras diferentes das usadas no documento.

No computador com o código da OMM, crie `perguntas.json` fora do repositório
público. Este exemplo usa identificadores fictícios; substitua pelos seus:

```json
[
  {
    "id": "identidade-do-original",
    "scope": "meu-projeto",
    "kind": "memory",
    "query": "Como confirmar que o arquivo original não mudou?",
    "expected_ids": ["id-completo-do-registro-vigente"],
    "forbidden_ids": ["id-completo-do-registro-superado"]
  },
  {
    "id": "guia-de-compilacao",
    "scope": "meu-projeto",
    "kind": "source",
    "query": "Como compilar?",
    "expected_ids": ["sources/meu-projeto/docs/BUILD.md"]
  },
  {
    "id": "assunto-ausente",
    "scope": "meu-projeto",
    "kind": "memory",
    "query": "expressaoexclusivamentesinteticaausente",
    "expected_empty": true
  }
]
```

No terminal, entre na pasta do código e execute, trocando os caminhos:

```bash
python3 benchmarks/project_retrieval_eval.py \
  --root /caminho/dados-omm \
  --cases /caminho/privado/perguntas.json \
  --top-k 5 --min-recall 1
```

O comando procura em uma OMM existente. Ele não altera memórias ou documentos;
pode atualizar o índice derivado e o arquivo de coordenação de acesso.
Cada pergunta consulta apenas o escopo informado. `expected_ids` contém alvos
que devem aparecer; `forbidden_ids` contém alvos que não podem aparecer.
Para fontes, use caminhos completos relativos à pasta de dados, como no exemplo.
Até 200 perguntas, 20 alvos por pergunta e 20 resultados por consulta.

O resultado mostra a proporção dos alvos encontrados (`recall_at_k`), a posição
do primeiro alvo correto e o tempo de consulta. Não imprime perguntas, textos,
caminhos ou IDs dos documentos encontrados; só os IDs escolhidos para os casos
e as métricas. Escolha IDs de casos sem informações pessoais.
O código de saída é 0 se todos atingirem os limites, 1 se algum falhar e 2 para
entrada inválida. Não ajuste as respostas esperadas para esconder uma falha.

Esse teste avalia a busca lexical. Ele não comprova a interpretação do modelo,
a qualidade de uma resposta final nem a cobertura total do projeto. Revise os
alvos quando o projeto mudar. Guarde a lista e os resultados em local privado.
Compare a mesma lista antes e depois de uma mudança. O conjunto sintético
`benchmarks/retrieval_eval.py` continua disponível para verificações genéricas.

## Conferir o tamanho das instruções MCP

Com a dependência opcional MCP instalada, execute na pasta do código:

```bash
python3 benchmarks/mcp_catalog_eval.py
```

O comando usa dados temporários inventados, sem chamar modelos. Mostra os
caracteres das instruções iniciais e do catálogo de ferramentas do SDK, além da
comparação com o guia extenso anterior. A comparação conjunta inclui o custo
da nova ferramenta de guia. O cliente pode serializar, carregar ou repetir
instruções de outra forma. Não interprete essa redução como percentual de
economia da conversa inteira ou do plano contratado.

## Medir tokens de verdade

A OMM vê o texto das chamadas MCP. O cliente também envia ao modelo instruções,
histórico, descrições de ferramentas e respostas de outras ferramentas. Pode
reutilizar partes por cache e chamar subagentes. Esses dados não chegam todos à OMM.

Para uma comparação útil:

1. Escolha tarefas pequenas e repetíveis, com critérios de aprovação definidos.
2. Use o mesmo estado inicial do código e dos dados, modelo, esforço e ferramentas.
3. Execute em sessões independentes a versão anterior e a nova. Alterne a ordem
   e faça mais de uma repetição para reduzir efeitos de cache e variação do modelo.
4. Guarde os contadores oficiais de entrada, saída e cache de cada execução,
   incluindo tentativas, compactações e subagentes. Registre a versão e a origem
   dos números. Se o cliente não expuser alguma parte, marque-a como desconhecida.
5. Confira os critérios de aprovação nas duas versões. Compare consumo por tarefa
   aprovada, número de tentativas, tempo e qualidade; uma resposta incompleta não
   conta como economia.

Use `(tokens anteriores - tokens novos) / tokens anteriores` somente para
contadores comparáveis e cobertura equivalente. Separe entrada, saída e cache;
as convenções dos provedores diferem. Não some novamente campos de detalhe já
incluídos no total. Para dinheiro, aplique a tabela do modelo e as categorias
corretas de cache; para cota de assinatura, não converta tokens em porcentagem
sem uma regra oficial do provedor.

As APIs documentam campos de uso e cache: [OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching)
e [Claude](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
O mesmo nível de informação pode não estar disponível no aplicativo usado.
A OMM não inventa contadores ausentes e não transforma caracteres divididos por
quatro em tokens faturados. Não há coleta automática dos históricos privados
ou das contas dos provedores nesta avaliação.

## Comparar atualizações pequenas da busca

Na pasta do código, execute `python3 benchmarks/incremental_eval.py`. O teste cria
2.000 documentos inventados em uma pasta temporária, prepara a busca e mede uma
alteração de um documento. Não lê projetos reais. Compare na mesma máquina;
os tempos desse exemplo não são promessa de desempenho nem economia de tokens.

A busca passa a reler apenas documentos novos ou alterados e remove os trechos
dos documentos apagados. Na primeira atualização da versão, é necessário criar
o controle dos arquivos. Vetores e lotes semânticos já salvos são preservados.
Ainda há conferência dos metadados do acervo, mesmo em uma atualização pequena.
Alterar outro projeto não invalida por si só os vetores do projeto consultado.
As versões antigas serão reconhecidas como desatualizadas uma vez; o cálculo
continua aproveitando vetores com o mesmo conteúdo.

## Entender as esperas

`usage_report` passa a mostrar faixas de duração por ferramenta e projeto. Os
campos `p50_upper_ms` e `p95_upper_ms` são limites superiores de faixas, não tempos
exatos. Por exemplo, 500 significa que o ponto medido caiu na faixa até 500 ms.
O campo `observed_calls` mostra quantas chamadas têm essa medição; chamadas de
versões anteriores não foram reconstruídas. `null` no limite superior significa
que a faixa ultrapassou 120 segundos. Os tempos internos também podem se
sobrepor e não devem ser somados como duração total de uma tarefa.

## Saber quando compactar

O relatório de desempenho mostra `index_pages.free_bytes_estimate` e
`free_percent`: espaço interno que o banco pode reutilizar sem crescer. A sugestão
`compaction_recommended` aparece a partir de 256 MiB e 25% livres. É uma sugestão,
não uma falha nem uma limpeza automática.

Em um período sem reconstrução semântica, execute no container da aplicação:

```bash
python -m omm --root /data semantic-compact
```

O comando aguarda uma reconstrução já em andamento antes de compactar. Enquanto
compacta, operações podem esperar. Não interrompa um trabalho ativo só porque o
arquivo ainda ocupa espaço; a compactação não altera memórias e fontes.
