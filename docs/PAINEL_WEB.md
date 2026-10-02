# Painel da OMM

O painel é uma página simples para consultar e organizar a memória no navegador. Você não precisa criar uma conta em outro serviço. Se quiser, pode configurar um usuário e uma senha para abrir o painel.

## Abrir no computador

Com a OMM ligada, abra este endereço no mesmo computador:

```text
http://localhost:8001
```

O painel mostra quantas anotações estão ativas, quantas dúvidas continuam abertas, quanto espaço os arquivos ocupam e quantas skills estão disponíveis. Isso não mostra nem altera a cota do assistente de IA.

Você pode buscar por palavras, filtrar por projeto e abrir as fontes registradas. “Arquivar” tira a anotação das pesquisas; “Restaurar” a torna ativa outra vez. Uma anotação arquivada também pode ser apagada dos arquivos atuais. O painel pede confirmação e informa que cópias antigas podem continuar no histórico Git.

O painel também mostra as regras cadastradas, a passagem de trabalho mais recente, as skills e os ajudantes de cada projeto. Na primeira versão, regras, passagens, skills e papéis de ajudante são apenas consultados aqui; edite os arquivos ou use o MCP para alterá-los.

## Abrir de outro computador

Por padrão, só o computador que executa a OMM consegue abrir o painel. Para permitir acesso de outros computadores da sua rede, defina no arquivo `.env`:

```env
OMM_BIND_ADDRESS=0.0.0.0
```

Depois, reinicie a Stack e abra `http://IP-DO-SERVIDOR:8001`, trocando `IP-DO-SERVIDOR` pelo endereço do computador que executa a OMM.

Para pedir uma senha no painel, defina `OMM_WEB_USERNAME` e `OMM_WEB_PASSWORD` no `.env` e reinicie a OMM. O navegador pedirá esses dados ao abrir o painel. Essa senha sozinha não protege o caminho da senha pela rede; para acessar de fora de casa, use um endereço HTTPS protegido.

A senha do painel não protege a conexão usada pelos assistentes. Não abra as portas da OMM diretamente para a internet. Para acessar de fora da sua rede, peça a alguém que administra a rede para criar um endereço HTTPS que peça login e proteja a conexão. A OMM ainda não pede senha própria ao MCP.

## O que pode ser reconstruído?

As anotações, regras e passagens são arquivos normais dentro de `memory/` e ficam no backup Git. O índice é recriado automaticamente ao iniciar o OMM. O painel só altera o estado da anotação; os arquivos canônicos continuam sendo a memória principal.
