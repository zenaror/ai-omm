# Painel da OMM

O painel é uma página simples para consultar e organizar a memória no navegador. Você não precisa criar uma conta em outro serviço. Se quiser, pode configurar um usuário e uma senha para abrir o painel.

## Abrir no computador

Com a OMM ligada, abra este endereço no mesmo computador:

```text
http://localhost:8001
```

O painel mostra quantas anotações estão ativas, quantas dúvidas continuam abertas, quanto espaço os arquivos ocupam e quantas skills estão disponíveis. Isso não mostra nem altera a cota do assistente de IA.

Você pode buscar por palavras, filtrar por projeto e abrir as fontes registradas. Ao pesquisar, abra **Prévia do contexto** para conferir o texto que a OMM entregaria ao agente e uma estimativa aproximada de tokens. O número varia conforme o modelo; não representa a cota usada na conversa. “Arquivar” tira a anotação das pesquisas; “Restaurar” a torna ativa outra vez. Uma anotação arquivada também pode ser apagada dos arquivos atuais. O painel pede confirmação e informa que cópias antigas podem continuar no histórico Git.

O painel também mostra as regras cadastradas, a passagem de trabalho mais recente, as skills e os ajudantes de cada projeto. Skills e papéis podem ser criados ou atualizados pelas ferramentas MCP `save_skill` e `save_role`. Para atualizar um arquivo existente, o agente precisa primeiro ler a versão atual; a OMM recusa uma alteração baseada numa cópia desatualizada. As regras e passagens ainda são consultadas por este painel.

## Abrir de outro computador

Por padrão, só o computador que executa a OMM consegue abrir o painel. Para permitir acesso de outros computadores da sua rede, defina no arquivo `.env`:

```env
OMM_BIND_ADDRESS=0.0.0.0
```

Depois, reinicie a Stack e abra `http://IP-DO-SERVIDOR:8001`, trocando `IP-DO-SERVIDOR` pelo endereço do computador que executa a OMM.

Para pedir uma senha no painel, defina `OMM_WEB_USERNAME` e `OMM_WEB_PASSWORD` no `.env` e reinicie a OMM. O navegador pedirá esses dados ao abrir o painel. Essa senha sozinha não protege o caminho da senha pela rede; para acessar de fora de casa, use um endereço HTTPS protegido.

A senha do painel não protege a conexão usada pelos assistentes. O token `OMM_MCP_TOKEN` é opcional; configure-o se quiser exigir uma chave nas chamadas MCP. Veja o guia [Ligar um assistente](USAR_MCP.md#proteger-o-mcp-quando-usar-pela-rede). Não abra as portas da OMM diretamente para a internet; use HTTPS ou VPN ao acessar de fora da sua rede.

## O que pode ser reconstruído?

As anotações, regras e passagens são arquivos normais dentro de `memory/` e ficam no backup Git. Os documentos importados também são arquivos normais em `sources/`. O índice é recriado automaticamente ao iniciar a OMM; ele é uma cópia de trabalho para acelerar buscas.

# Revisar documentos importados

No painel, escolha um projeto e abra **Materiais disponíveis**. A lista permite remover uma fonte dos arquivos atuais da OMM. O sistema pede confirmação e confere se o arquivo continua igual ao que foi listado.

Remover uma fonte tira seu conteúdo das buscas atuais depois que o índice é reconstruído. **Isso não apaga versões antigas que já foram salvas no histórico do Git.** Se o documento só precisa de correção, use a ferramenta `replace_source` do MCP para salvar uma versão revisada no mesmo caminho.
