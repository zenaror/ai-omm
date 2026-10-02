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

**Busca atual:** busca textual FTS5 com classificação por relevância. Termos da consulta podem encontrar registros que correspondam a parte dela. A busca semântica continua sendo uma extensão opcional futura, não uma dependência para instalar ou operar a OMM.

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

**Acesso web:** o painel aceita senha HTTP Basic configurável. O endpoint MCP por HTTP ainda não autentica pedidos por conta própria; acesso remoto ao MCP e ao painel deve passar por um proxy reverso com autenticação e HTTPS.
