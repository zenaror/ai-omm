# Ligar um assistente à OMM

O MCP conecta o assistente às ferramentas da OMM. Depois da conexão, também ensine o agente quando consultar e atualizar a memória. Veja o [guia curto para agentes](USAR_OMM_NOS_AGENTES.md).

## Caminho rápido

1. Na pasta da OMM, inicie o serviço:

   ```sh
   docker compose up -d
   ```

   Se usar Podman, siga [Usar a OMM com Podman](USAR_PODMAN.md).

2. Cadastre `http://localhost:8000/mcp` no assistente que roda no mesmo computador.

   No Codex:

   ```sh
   codex mcp add omm --url http://localhost:8000/mcp
   ```

   No Claude Code, para todos os projetos do usuário:

   ```sh
   claude mcp add --transport http --scope user omm http://localhost:8000/mcp
   ```

3. Em uma conversa nova, peça ao agente para confirmar que vê as ferramentas. Depois, use o texto do [guia para agentes](USAR_OMM_NOS_AGENTES.md) no `AGENTS.md` do projeto.

Se algo não funcionar, execute o diagnóstico na pasta da OMM:

```sh
omm doctor
```

Em uma instalação Docker Compose, use o terminal do computador:

```sh
docker compose exec omm python -m omm --root /data doctor
```

O comando mostra o que está pronto e o que precisa de atenção. Ele só confere; não altera nem sincroniza arquivos.

O MCP fica disponível enquanto a OMM está ligada. Use `docker compose down` para desligá-la. `localhost` só funciona para programas no mesmo computador; assistentes hospedados na nuvem não conseguem acessá-lo diretamente.

Para economizar contexto, `context` devolve um resumo curto e não inclui documentos-fonte automaticamente. Peça trechos apenas quando precisar conferir um documento.

## Busca semântica: procurar pelo assunto

A busca comum encontra palavras que aparecem na anotação. A busca semântica tenta encontrar **o mesmo assunto, mesmo quando a pergunta usa outras palavras**. Por exemplo, “como evito perder decisões entre conversas?” pode encontrar uma anotação sobre memória compartilhada. Ela ajuda a procurar; não garante que entendeu certo. Confira a anotação e sua origem antes de confiar nela.

Para fazer essa busca, a OMM usa o Ollama com o modelo `embeddinggemma`. O modelo transforma textos em números para comparar os assuntos. Ele não escreve respostas. A OMM continua sendo dona dos arquivos; o índice de busca pode ser recriado.

### Ativar na stack Docker ou Portainer

Na configuração da stack, junte `semantic` aos perfis que você já usa. Se também usa backup, por exemplo, fica `backup,semantic`. Adicione estas variáveis:

```dotenv
COMPOSE_PROFILES=semantic
OMM_SEMANTIC_ENABLED=true
OMM_EMBEDDING_URL=http://ollama:11434/api/embed
OMM_EMBEDDING_MODEL=embeddinggemma
OMM_EMBEDDING_TIMEOUT=10
```

Se já usa o perfil `backup`, mantenha os dois: `COMPOSE_PROFILES=backup,semantic`. Depois, atualize a stack. Ela inicia o Ollama e baixa o modelo na primeira vez (cerca de 622 MB). A tarefa de instalação pode aparecer como concluída/parada no Portainer; isso é normal. O modelo fica em um volume separado e sobrevive a atualizações da aplicação.

A OMM conversa com o Ollama por dentro da rede Docker. A porta 11434 não fica aberta para os outros computadores. A configuração inicial usa CPU; GPU não é necessária para começar. Se o LXC estiver com pouca memória livre, confira o uso antes de aumentar o limite.

O primeiro uso pode demorar enquanto a OMM prepara o índice. Depois, ela reaproveita o que não mudou. Só a ferramenta `semantic_search` usa o Ollama; a busca normal continua local e não chama esse serviço. O texto das memórias e fontes é enviado ao Ollama local para gerar as comparações; não é enviado a um serviço externo por esta configuração. Se trocar o endereço por um serviço remoto, os textos sairão da sua rede. A busca semântica pode ficar mais lenta em coleções muito grandes. O modelo ocupa cerca de 622 MB e precisa de Ollama 0.11.10 ou mais recente ([detalhes do modelo](https://ollama.com/library/embeddinggemma)).

Quando habilitada, o agente pode chamar `semantic_search` se a busca comum não encontrar algo que parece estar na memória. Na linha de comando, use `omm semantic-search "sua pergunta"`; para refazer manualmente o índice, use `omm semantic-rebuild`.

## Perfis da stack

`COMPOSE_PROFILES` escolhe quais serviços extras a stack inicia. Os nomes disponíveis são:

- vazio: só a aplicação OMM;
- `backup`: inicia o serviço que faz cópias no Git. Também é preciso `OMM_GIT_BACKUP_ENABLED=true`;
- `semantic`: inicia Ollama e baixa o modelo de busca. Também é preciso `OMM_SEMANTIC_ENABLED=true`;
- `backup,semantic`: inicia os dois extras.

Escreva o valor em `.env` ou nas variáveis da stack no Portainer. Se já usa `backup`, acrescente `,semantic`; não apague `backup`.

## Dados e atualizações

O código da OMM e os dados ficam separados. `OMM_DATA_PATH` aponta para a pasta de dados, que contém memórias, skills e documentos. A busca é um índice reconstruível; ela não substitui esses arquivos.

Para atualizar o código, entre na pasta da aplicação e execute:

```sh
git pull --ff-only
docker compose up --build -d
```

Com os dados em uma pasta persistente, atualizar a aplicação não apaga a memória.

## Backup no Git (opcional)

O backup guarda versões das memórias, skills e documentos. Por padrão, ele fica desligado. Para configurá-lo, copie `.env.example` para `.env` e ajuste:

```dotenv
COMPOSE_PROFILES=backup
OMM_GIT_BACKUP_ENABLED=true
OMM_GIT_BACKUP_RESTORE=true
OMM_GIT_BACKUP_PUSH=true
OMM_GIT_BACKUP_REPOSITORY_URL=https://git.example.com/usuario/omm-dados.git
OMM_GIT_BACKUP_BRANCH=main
OMM_GIT_BACKUP_USERNAME=SEU_USUARIO
OMM_GIT_BACKUP_TOKEN=SEU_TOKEN
```

O endereço pode apontar para GitHub, GitLab, Gitea ou outro servidor Git. Para manter a cópia só neste computador, deixe o envio desligado e use uma pasta de dados que seja repositório Git. Nunca publique o token nem o coloque no endereço do repositório.

O serviço faz backup no horário definido por `OMM_GIT_BACKUP_SCHEDULE` (padrão: todo dia às 03:00, horário de São Paulo). Para conferir mensagens, use `docker compose logs -f omm-backup`.

Na primeira inicialização, o restore automático baixa os dados se a pasta estiver vazia. O índice de busca é recriado. Se os dados já existirem, a OMM não os substitui automaticamente.

No painel, **Sincronizar backup** salva as mudanças locais e busca novidades. **Atualizar** apenas recarrega a página. Se uma mudança não puder ser juntada com segurança, a OMM preserva uma cópia em `memory/imports/sync-recovery/` para revisão.

### Restaurar manualmente (avançado)

Use somente com uma pasta de dados vazia:

```sh
docker compose --profile backup run --rm --no-deps omm-backup \
  python -m omm --root /data restore \
  --from https://git.example.com/usuario/omm-dados.git --branch main
```

Para uma instalação em servidor, veja o guia avançado [Instalar no Proxmox](INSTALAR_PROXMOX.md).

## Segurança do painel

O painel pode ser protegido com usuário e senha no `.env`:

```dotenv
OMM_WEB_USERNAME=omm
OMM_WEB_PASSWORD=ESCOLHA_UMA_SENHA_LONGA
```

Essa senha protege o painel, mas não o MCP. Mantenha as conexões em uma rede privada ou VPN. Não exponha as portas à internet sem HTTPS e uma camada de proteção adequada.

### Proteger o MCP quando usar pela rede

No mesmo computador, deixe `OMM_MCP_TOKEN` vazio. Para conexões MCP pela rede, crie uma chave longa:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Coloque a chave em `OMM_MCP_TOKEN` no `.env` da OMM e reinicie o serviço. O assistente também precisa enviar a mesma chave no cabeçalho `Authorization`. No Codex, configure a variável no ambiente em que o Codex é iniciado e use:

```sh
codex mcp add omm --url http://SERVIDOR:8000/mcp --bearer-token-env-var OMM_MCP_TOKEN
```

No Claude Code, use:

```sh
claude mcp add --transport http --scope user \
  --header "Authorization: Bearer $OMM_MCP_TOKEN" \
  omm http://SERVIDOR:8000/mcp
```

Troque `SERVIDOR` pelo nome ou endereço do computador. A chave dá acesso às ferramentas de leitura e escrita da OMM; compartilhe-a apenas com assistentes confiáveis. A chave não substitui HTTPS ou VPN em redes que você não controla.
