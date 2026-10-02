# Usar a OMM com Podman

O Podman pode montar a mesma imagem da OMM. Não é necessário manter um Dockerfile separado: o arquivo `Dockerfile` atual é aceito pelo `podman build`. O Podman usa o mesmo formato de imagem de container. O arquivo `.dockerignore` também evita enviar ao build arquivos que o programa não precisa, como as configurações locais e a memória do usuário.

## O que você precisa

- Podman instalado no computador ou servidor.
- Um programa auxiliar de Compose. Ele lê o arquivo `compose.yaml` e inicia os serviços descritos nele.

Confira se os dois estão disponíveis:

```sh
podman --version
podman compose version
```

Se o segundo comando disser que não encontrou um provedor, instale pelo gerenciador de programas da sua distribuição um provedor chamado `podman-compose` ou Docker Compose. Depois repita o comando. O `podman compose` é um atalho do Podman para esse provedor; ele não é o próprio programa Compose.

## Iniciar a OMM

Abra um terminal na pasta da OMM. Se ainda não tiver um arquivo `.env`, crie um a partir do exemplo:

```sh
cp .env.example .env
```

No `.env`, confira `OMM_DATA_PATH`. Essa é a pasta onde ficam as memórias. Para que a OMM possa gravar nelas, a conta Linux que executa o Podman precisa ter acesso de leitura e escrita a essa pasta. Se ela já contém seu backup, não apague nem troque a pasta.

Inicie o serviço:

```sh
podman compose up --build -d
```

O Podman vai criar a imagem e iniciar a OMM em segundo plano. Depois, abra:

- MCP para os assistentes: `http://localhost:8000/mcp`
- Painel no navegador: `http://localhost:8001`

Para conferir se o serviço está ligado:

```sh
podman compose ps
```

Para ver mensagens do serviço:

```sh
podman compose logs -f omm
```

Para parar a OMM:

```sh
podman compose down
```

Isso para os serviços. As memórias continuam na pasta `OMM_DATA_PATH`.

## Ligar o backup agendado (opcional)

Se você também quer que a OMM faça commits Git no horário programado, ajuste o `.env`:

```dotenv
COMPOSE_PROFILES=backup
OMM_GIT_BACKUP_ENABLED=true
```

O backup usa a mesma pasta de dados. Um serviço remoto, como GitHub, GitLab ou Gitea, só é necessário se você quiser enviar uma cópia para outro servidor. Veja [Ligar um assistente à OMM](USAR_MCP.md#backup-automático-no-git-opcional) para as outras opções.

Inicie com o perfil de backup:

```sh
podman compose --profile backup up --build -d
```

Para ver as mensagens do backup:

```sh
podman compose --profile backup logs -f omm-backup
```

## Atualizar a OMM

Na pasta do código da OMM, baixe a versão mais nova e recrie a imagem:

```sh
git pull --ff-only
podman compose up --build -d
```

Se o backup agendado estiver ativo, mantenha também `--profile backup` no comando. As memórias permanecem na pasta `OMM_DATA_PATH`; a imagem nova atualiza o programa.

## Se aparecer “permission denied” no Linux

Isso quer dizer que o Podman não tem permissão para abrir a pasta de dados. Confira se `OMM_DATA_PATH` aponta para o lugar certo e se a mesma conta que executa o Podman consegue criar e apagar um arquivo de teste nessa pasta. Não resolva o problema apagando os dados ou dando acesso irrestrito a todos os usuários.

Em computadores com SELinux, o sistema pode bloquear o acesso mesmo quando as permissões parecem corretas. SELinux é uma proteção extra do Linux. Nesse caso, peça ao administrador para configurar o rótulo de acesso compartilhado (`:z`) na montagem da pasta de dados para os dois serviços da OMM. O rótulo em minúsculo é usado porque o serviço principal e o backup agendado compartilham a mesma pasta. Não aplique essa opção a uma pasta usada por outros containers sem entender o efeito.

## Acesso pela rede

Por padrão, as portas ficam abertas apenas no mesmo computador. Para usar o MCP e o painel de outro computador, configure `OMM_BIND_ADDRESS=0.0.0.0` no `.env` e reinicie os serviços. Isso deixa as portas visíveis na rede; faça isso apenas numa rede privada e configure a proteção descrita em [PAINEL_WEB.md](PAINEL_WEB.md). O MCP por HTTP ainda não tem senha própria.

## Referências

- [Como o comando `podman compose` funciona](https://docs.podman.io/en/stable/markdown/podman-compose.1.html): ele encaminha os comandos a um provedor Compose instalado.
- [Criar imagens com `podman build`](https://docs.podman.io/en/stable/markdown/podman-build.1.html): o Podman aceita arquivos chamados `Dockerfile` ou `Containerfile`.
- [Montagens de pasta do Podman](https://docs.podman.io/en/latest/markdown/podman-run.1.html): explica permissões e os rótulos SELinux `z` e `Z`.
