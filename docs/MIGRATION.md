# Como trazer a memória de um projeto para o OMM

Projetos costumam espalhar informações entre instruções, anotações, testes e conversas. O OMM reúne o que vale a pena lembrar e mantém junto a origem de cada informação.

## O que trazer

| Onde costuma estar | O que guardar no OMM |
| --- | --- |
| `CLAUDE.md` ou `AGENTS.md` | Regras que os assistentes devem seguir; preserve o arquivo completo em `sources/<projeto>/`. |
| `HANDOFF.md` | Onde o trabalho parou e o que fazer depois. |
| `EVIDENCE.md`, logs, capturas e resultados de teste | O que foi observado e onde encontrar a prova. Mantenha os arquivos originais no projeto. |
| `DEVLOG.md` | Decisões e descobertas que ainda ajudam o projeto. |
| Anotações “não redescobrir” ou “não presumir” | O que já foi investigado e o que ainda não está comprovado. |
| Uma ferramenta RAG | Preserve documentos úteis em `sources/<projeto>/`; a busca da OMM indexa esses arquivos e pode ser recriada. O índice antigo pode ser descartado. |
| Conversas separadas de planejamento e execução | Um coordenador pode chamá-las como ajudantes da mesma conversa quando a tarefa se beneficiar da divisão. Para tarefas simples, o coordenador trabalha sozinho. |
| Muitas conversas do mesmo projeto | Frentes de trabalho separadas, usando a mesma memória compartilhada. |
| Conhecimento especializado | Uma skill de especialista, além das informações confirmadas e suas fontes na memória do projeto. |

## Regras, handoffs e projetos com busca

Um arquivo de regras descreve como trabalhar; um handoff mostra onde uma tarefa parou; uma evidência registra o que foi observado. Traga cada tipo para seu lugar e mantenha o link para o documento original. Não misture uma instrução antiga com o estado atual do projeto.

Uma ferramenta RAG pode ajudar a localizar documentos originais. A OMM guarda as anotações selecionadas como memória canônica e usa seu próprio mecanismo de busca para encontrá-las. Um RAG externo não é uma segunda memória canônica nem prova de uma afirmação. Não é preciso copiar o índice inteiro.

O mesmo princípio vale para `CLAUDE.md`, `AGENTS.md`, `HANDOFF.md`, `EVIDENCE.md`, `DEVLOG.md`, contratos congelados e regras “não presumir”: preserve as cópias completas sob `sources/<projeto>/`, mantenha a origem e registre as conclusões duradouras separadamente em `memory/`.

## Como escolher o que guardar

Guarde informações que ajudam o próximo trabalho: decisões, descobertas, regras importantes, resultados de testes e perguntas ainda sem resposta. Para cada anotação, tente registrar de onde ela veio.

Não copie conversas inteiras sem revisão. Uma conversa pode misturar fatos, ideias temporárias e instruções que só valiam naquele momento. Selecione o que continua útil e mantenha uma referência à conversa original.

Também não transforme uma dúvida em certeza. O OMM permite indicar se algo é fato, observação, hipótese, decisão, regra ou pergunta em aberto. Esses nomes aparecem em inglês nos comandos, mas seus significados são explicados no [README](../README.md).

## Exemplo

Em vez de guardar apenas “o teste falhou”, registre qual teste falhou, em qual versão, o que foi observado e onde está o resultado. Assim, outra pessoa ou assistente consegue entender o contexto sem repetir a investigação às cegas.

## Limites da primeira versão

O OMM procura anotações canônicas e trechos de documentos Markdown preservados em `sources/`. Conversas importadas são fontes históricas; os trechos de busca apontam para elas e não viram fatos canônicos automaticamente. O OMM não cria conversas ou subagentes; o assistente coordenador usa os papéis registrados e chama os ajudantes disponíveis na plataforma.
