# Histórico de mudanças

Mudanças que afetam quem usa ou mantém a OMM. O histórico de commits guarda os
detalhes da implementação.

## [Não lançado]

### Adicionado

- Imagens como evidência: envio pelo painel ou em lote, descrições pesquisáveis,
  prévias para os assistentes e originais preservados no backup. Atualize também
  o serviço de backup antes de enviar imagens.

- Consulta, criação e atualização de frentes de trabalho pelo MCP. Atualizações
  exigem a versão lida e preservam o histórico.
- Categorias de falha no relatório de uso, sem guardar mensagens ou conteúdo privado.
- Opção para reconstruir a busca semântica só dos projetos escolhidos.
- Testes automáticos antes da publicação da imagem.

### Corrigido

- Projetos sem mapa próprio de agentes recebem as regras globais na mesma consulta,
  em vez de um erro que exige outra tentativa.
- Um teste antigo esperava que memórias substituídas voltassem à busca comum.
  A conferência agora preserva a regra de mantê-las fora dessa busca.

### Alterado

- Frentes aceitam o estado pausado. Use a mesma versão atualizada da OMM em todos
  os processos que acessam esses dados; versões antigas não consolidam suas revisões.
