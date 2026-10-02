# Instalar a OMM no Proxmox (guia avançado)

Este guia prepara um container Debian para rodar a OMM em Docker. O código fica em `/opt/ai-omm`; os dados persistentes ficam em um volume separado no armazenamento `NOME_DO_ARMAZENAMENTO`. Assim, é possível atualizar o programa sem substituir a memória.

## 1. Criar o container

Execute como `root` no terminal do nó Proxmox. O script escolhe um ID livre e a versão Debian 12 disponível. Ele reserva 2 CPUs, 4 GB de memória, 32 GB para o sistema e 64 GB para memória, skills e projetos futuros.

```sh
set -euo pipefail

CTID="$(pvesh get /cluster/nextid)"
TEMPLATE="$(pveam available --section system | awk '$2 ~ /^debian-12-standard_.*_amd64\.tar\.zst$/ {print $2; exit}')"
if [ -z "$TEMPLATE" ]; then
  echo "Não encontrei um template Debian 12 amd64. Confira os templates disponíveis no Proxmox."
  exit 1
fi

if ! pveam list local | awk '{print $1}' | grep -Fxq "local:vztmpl/$TEMPLATE"; then
  pveam download local "$TEMPLATE"
fi
pct create "$CTID" "local:vztmpl/$TEMPLATE" \
  --hostname omm \
  --cores 2 \
  --memory 4096 \
  --swap 2048 \
  --rootfs NOME_DO_ARMAZENAMENTO:32 \
  --mp0 NOME_DO_ARMAZENAMENTO:64,mp=/srv/omm-data,backup=1 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp,firewall=1 \
  --features nesting=1,keyctl=1 \
  --unprivileged 1 \
  --onboot 1 \
  --start 1

echo "Container criado com ID $CTID"
```

Antes de executar, troque `NOME_DO_ARMAZENAMENTO` pelo nome do armazenamento local no seu Proxmox e confirme em **Datacenter → Storage** que ele aceita conteúdo **Container** (`rootdir`). O comando usa esse armazenamento tanto para o sistema quanto para o volume de dados. A opção `backup=1` inclui o volume de dados quando o Proxmox fizer backup do container; o destino e a agenda desses backups são configurados no Proxmox separadamente. Os parâmetros `nesting` e `keyctl` habilitam recursos usados pelo Docker dentro do LXC. O container continua não privilegiado.

O container pega um endereço pelo DHCP. Crie uma reserva DHCP no roteador para manter o endereço estável.

## 2. Instalar Docker dentro do LXC

Entre no container pela interface do Proxmox ou execute `pct enter ID`, trocando `ID` pelo número mostrado ao final da criação. Dentro do Debian, instale o Docker Engine pelo repositório oficial:

```sh
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: bookworm
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin git
```

## 3. Instalar e restaurar a OMM

Ainda dentro do LXC:

```sh
git clone https://github.com/SEU_USUARIO/ai-omm.git /opt/ai-omm
cd /opt/ai-omm
cp .env.example .env
```

Edite `/opt/ai-omm/.env` e configure estas opções:

```dotenv
COMPOSE_PROFILES=backup
OMM_DATA_PATH=/srv/omm-data
OMM_BIND_ADDRESS=0.0.0.0
OMM_GIT_BACKUP_ENABLED=true
OMM_GIT_BACKUP_RESTORE=true
OMM_GIT_BACKUP_PUSH=true
OMM_GIT_BACKUP_REMOTE=origin
OMM_GIT_BACKUP_REPOSITORY_URL=https://git.example.com/usuario/omm-dados.git
OMM_GIT_BACKUP_BRANCH=main
OMM_GIT_BACKUP_USERNAME=SEU_USUARIO_DO_GIT
OMM_GIT_BACKUP_TOKEN=COLOQUE_SEU_TOKEN_AQUI
```

O token precisa poder ler e escrever no repositório privado de backup. `OMM_GIT_BACKUP_RESTORE=true` faz a primeira inicialização buscar a memória desse endereço. A restauração só substitui o esqueleto vazio criado pela OMM; se já houver informações, a inicialização para sem apagá-las. Para usar somente Git local, configure `OMM_GIT_BACKUP_PUSH=false` e deixe URL, usuário e token vazios. O OMM também aceita URLs de GitHub, GitLab e outros servidores Git; o provedor define o tipo de token necessário. Proteja o arquivo `.env` para que só a conta administradora possa lê-lo.

### Busca semântica (opcional)

A busca normal procura palavras. A busca semântica tenta achar o mesmo assunto quando a pergunta usa palavras diferentes. Para ativá-la, use o Ollama que a stack inicia e acrescente ao `.env`:

```dotenv
COMPOSE_PROFILES=backup,semantic
OMM_SEMANTIC_ENABLED=true
OMM_EMBEDDING_URL=http://ollama:11434/api/embed
OMM_EMBEDDING_MODEL=embeddinggemma
OMM_EMBEDDING_TIMEOUT=10
```

Na primeira subida, a stack baixa o modelo (cerca de 622 MB) para um volume que continua existindo após atualizações. Por padrão, o processamento começa pela CPU. Para usar uma GPU Intel Arc disponível no LXC, inclua `compose.intel-gpu.yaml` como **Additional paths** na stack Git do Portainer; isso passa `/dev/dri` ao Ollama e ativa Vulkan. O LXC também precisa expor esse caminho e dar acesso ao grupo `render`. Em LXC não privilegiado, o Proxmox pode mostrar a placa como `nobody`; nesse caso, é preciso mapear o grupo `render` do host para o LXC. Defina `OLLAMA_GPU_RENDER_GID` com o número do grupo `render` dentro do LXC (neste exemplo, `104`). Sem esse acesso, o dispositivo aparece no container, mas o Ollama recebe “Permission denied” e usa CPU. Se não quiser GPU, não inclua o arquivo adicional.

Como o exemplo permite abrir o painel na rede, também configure um usuário e uma senha exclusivos para ele. Acrescente estas linhas ao mesmo `.env` e troque a senha de exemplo por uma senha longa que você não usa em outro lugar:

```dotenv
OMM_WEB_USERNAME=omm
OMM_WEB_PASSWORD=troque-por-uma-senha-longa
```

Essa senha protege o painel, mas não a conexão dos assistentes. Mantenha o acesso MCP dentro da rede privada ou use uma VPN.

```sh
chmod 600 .env
docker compose --profile backup build
```

O restore automático ocorre quando os serviços sobem. Se preferir, também é possível restaurar manualmente para uma pasta vazia antes de iniciar a OMM:

```sh
docker compose --profile backup run --rm --no-deps omm-backup \
  python -m omm --root /data restore \
  --from "$OMM_GIT_BACKUP_REPOSITORY_URL" --branch main
```

Depois inicie a OMM e o backup agendado:

```sh
docker compose --profile backup up -d
```

O serviço de backup salva alterações todo dia às 03:00, horário de São Paulo. Para conferir se os serviços estão ligados, use `docker compose --profile backup ps`; para ver as mensagens do backup, use `docker compose --profile backup logs -f omm-backup`.

## Acesso dos projetos

Com `OMM_BIND_ADDRESS=0.0.0.0`, os computadores da rede podem acessar `http://IP-DO-LXC:8000/mcp`. Essa opção deixa a conexão dos assistentes visível na rede local. A OMM ainda não pede senha para essa conexão. Não encaminhe a porta 8000 para a internet. No firewall do Proxmox, permita o acesso apenas aos computadores ou à VPN que usam a OMM.

O painel usa a porta `8001`. Você pode exigir usuário e senha para ele preenchendo `OMM_WEB_USERNAME` e `OMM_WEB_PASSWORD` no `.env`. Essa senha não protege o MCP na porta `8000`. Para acesso fora de casa, use uma VPN ou peça a um administrador para configurar um endereço HTTPS protegido.

Este passo a passo instala Docker dentro do LXC. Para usar Podman em outro computador ou servidor Linux, veja [Usar a OMM com Podman](USAR_PODMAN.md). O uso de Podman dentro deste LXC não está coberto por este guia.

## Referências

- Manual do [`pct`](https://pve.proxmox.com/pve-docs-9-beta/pct.1.html): criação de LXC, armazenamento, mountpoints, container não privilegiado e recursos `nesting`/`keyctl`.
- [Guia do Proxmox VE](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf): tipos de conteúdo de armazenamento, incluindo `rootdir` e `backup`.
- [Instalação oficial do Docker Engine no Debian](https://docs.docker.com/engine/install/debian/).
