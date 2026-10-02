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

O MCP fica disponível enquanto a OMM está ligada. Use `docker compose down` para desligá-la. `localhost` só funciona para programas no mesmo computador; assistentes hospedados na nuvem não conseguem acessá-lo diretamente.

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
