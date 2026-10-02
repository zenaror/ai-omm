# Decisões da OMM

## Explicações simples para quem usa o programa

**Decisão:** guias, telas e mensagens destinadas a quem usa a OMM devem ser escritos em português do Brasil simples, no estilo ELI5. Explique siglas e termos técnicos quando aparecerem e dê passos que a pessoa possa seguir sem experiência prévia.

**Por quê:** conectar um assistente ou cuidar de uma memória não deveria exigir que a pessoa já conheça os detalhes técnicos por trás da ferramenta.

## Uma memória compartilhada, com partes globais e partes de projeto

**Decisão:** oferecer um serviço OMM compartilhado para agentes, com registros em escopos. Conhecimento de domínio reutilizável fica no escopo `global`; resultados, regras e evidências próprias continuam no escopo do projeto. Uma busca de projeto pode consultar os dois.

**Por quê:** copiar o mesmo fato para vários repositórios cria versões divergentes. Um registro central reduz essa duplicação sem misturar evidências específicas de projetos.

## Git guarda a memória; banco de dados acelera a busca

**Decisão:** os arquivos simples dentro de `memory/` continuam sendo a memória principal e são versionados pelo Git. O SQLite/FTS é só um índice local de busca. Ele pode ser reconstruído a partir dos arquivos.

**Por quê:** arquivos Git são fáceis de revisar, copiar, fazer backup e levar para outra máquina. Um índice rápido não deve ser a única cópia de uma decisão ou evidência.

**Busca atual:** busca textual FTS5 com classificação por relevância. A busca por significado é opcional; no Compose, o perfil `semantic` inicia Ollama em um serviço separado e baixa o modelo para um volume persistente. Continua desligada por padrão. Seus vetores ficam no índice descartável e podem ser refeitos dos arquivos canônicos. A porta do Ollama fica apenas na rede interna da stack.

## Código atualizável e memória local separada

**Decisão:** o caminho dos dados (`OMM_DATA_PATH`) é separado do código da aplicação. Memórias, papéis, skills e perfis personalizados pertencem ao repositório Git de dados, em um repositório Git separado. O backup agendado cria commits locais apenas de `memory/` e `skills/`; enviar esses commits a um remoto é opcional.

**Por quê:** a aplicação pode ser atualizada e reconstruída no Docker sem misturar seu código com as personalizações. Para atualizar o programa, atualize seu repositório e reconstrua a imagem; os dados continuam no repositório separado. A imagem não inclui dados pessoais.

## A restauração recupera arquivos e recria a busca

**Decisão:** incluir um comando de restauração que clona o repositório de backup para uma pasta vazia, valida os arquivos canônicos e reconstrói o índice descartável.

**Por quê:** ao trocar de computador ou migrar para um servidor doméstico, a pessoa precisa recuperar o histórico Git e as anotações sem depender do banco de busca antigo.

**Atualização segura:** quando a pasta de dados já é um checkout do mesmo remoto, a inicialização busca dados novos e só avança se não houver alterações locais e o histórico permitir um avanço direto. Se a cópia local mudou, ela é preservada e a sincronização fica para depois.

## MCP é a porta dos assistentes; Docker é opcional

**Decisão:** disponibilizar ferramentas OMM por MCP. Oferecer Docker Compose para manter uma instância compartilhada no computador, e conservar a CLI para uso sem servidor.

**Por quê:** MCP ajuda assistentes compatíveis a usar as mesmas operações. Docker simplifica manter o serviço ativo. Nenhum dos dois substitui os arquivos do Git.

**Compatibilidade de containers:** manter uma imagem OCI que possa ser construída a partir do `Dockerfile` tanto com Docker quanto com Podman. Os exemplos Docker continuam válidos; o Podman usa um provedor Compose instalado para ler `compose.yaml`.

**Limite atual:** o Compose publica o serviço apenas em `localhost`. Escrita coordenada entre várias máquinas ainda não faz parte desta versão. O backup automático local é opcional; o envio a um serviço remoto também é opcional.

**Concorrência local:** operações da memória, sincronização e backup agendado usam um bloqueio de arquivo compartilhado na pasta de dados. Isso evita que dois processos locais alterem ou façam commit dos mesmos arquivos ao mesmo tempo. Não transforma instâncias em máquinas diferentes em um sistema de escrita distribuída; continue sincronizando pelo Git.

**Acesso web:** o painel aceita senha HTTP Basic configurável. O MCP aceita um token estático opcional em `OMM_MCP_TOKEN`; quando preenchido, cada chamada HTTP precisa enviar `Authorization: Bearer ...`. O token permite ler e alterar a memória, então deve ser longo e guardado fora do Git. Em rede pública, use HTTPS ou VPN. A conexão `stdio` continua protegida pelo próprio computador e não usa esse token.

## Contexto curto e busca sob demanda

**Decisão:** o `context` MCP tem um limite de tamanho e não anexa documentos-fonte por padrão. Resultados de busca mostram trechos curtos; ferramentas separadas abrem a anotação, o papel ou a fonte inteira quando necessário.

**Por quê:** assim, perguntas pequenas não carregam documentos e papéis inteiros para dentro da conversa. A pessoa ou o agente pode pedir mais quando isso fizer diferença.

**Proteção e manutenção:** fontes e memórias recuperadas são dados, nunca comandos. Antes de guardar uma informação, o agente procura duplicatas; quando uma decisão muda, a nova versão é registrada e a antiga marcada como substituída.

**Busca substituível:** a OMM separa a busca dos arquivos canônicos. SQLite/FTS5 vem pronto e não precisa de outro serviço. Se a pessoa habilitar, a busca semântica usa um serviço compatível com embeddings do Ollama. Nenhuma busca muda o formato das memórias.

**Custo da busca semântica:** a busca compara os vetores existentes, mas mantém apenas os melhores resultados enquanto percorre o índice e só abre o texto dos trechos escolhidos. Isso evita guardar todos os textos candidatos e ordenar a coleção inteira. A comparação ainda percorre o índice completo; uma coleção muito grande pode pedir uma tecnologia de busca vetorial dedicada no futuro.

**Revisão antes de guardar:** agentes podem propor novas anotações; a pessoa aprova ou recusa no painel. A lista de possíveis semelhantes usa palavras como pista, não decide sozinha se há conflito. O arquivo `memory/proposals.jsonl` é dado canônico e segue no backup Git.

**Avaliação de busca:** manter um pequeno conjunto sintético, sem dados pessoais, para medir se buscas de exemplo encontram as memórias e fontes esperadas e quanto texto o contexto prepara. Isso ajuda a perceber regressões sem usar serviços externos.

## Índice derivado e atualização rápida

**Decisão:** guardar no índice uma marca das versões dos arquivos pesquisáveis. Ao iniciar, a OMM só reconstrói o índice quando as memórias ou documentos Markdown mudaram; `rebuild` continua disponível para refazê-lo manualmente.

**Por quê:** o primeiro início cria a busca; os próximos não precisam reler tudo se os arquivos continuam iguais. O Git e os arquivos canônicos seguem como fonte principal.

**Recuperação:** se o arquivo SQLite sumir, estiver incompleto ou não abrir, ele é recriado dos arquivos canônicos. Consultas e novas anotações também têm limites para evitar respostas ou registros gigantes.
