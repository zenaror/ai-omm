# Histórico de mudanças

Mudanças que afetam quem usa ou mantém a OMM. O histórico de commits guarda os
detalhes da implementação. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/).

## [Não lançado]

### Consertado

- O backup confirma o commit remoto após o envio, tenta novamente em falhas
  temporárias e distingue cópia local de backup enviado no painel. A espera
  da rede do backup automático não bloqueia novas memórias.

- O terminal separa os itens reaproveitados dos novos ou recalculados durante
  a preparação da busca semântica, sem sugerir que uma retomada apagou o índice.

- Uma ocupação temporária do banco de busca não é mais tratada como corrupção.
  Isso preserva os lotes semânticos salvos enquanto outra operação usa o banco.

- A preparação da busca semântica salva cada lote concluído e reaproveita o
  trabalho após uma interrupção. O terminal mostra o andamento, que também pode
  ser consultado de outra sessão, e o cálculo usa menos memória para os vetores.

- A reconstrução semântica pelo terminal libera as gravações da OMM enquanto
  calcula os embeddings. Reconstruções simultâneas aguardam em uma trava própria.

- Imagens e arquivos de recuperação deixam de ter cotas totais por projeto
  impostas por padrão. Cotas passam a ser opcionais, configuradas explicitamente;
  zero desativa a cota. O Compose e os exemplos seguem o mesmo padrão.

- Guardar uma memória ou mudar seu status não espera pela reconstrução das
  fontes da busca. Se a busca falhar, o dado salvo continua preservado.

- Aprovar sugestões guarda as decisões sem esperar pela reconstrução de todas
  as fontes da busca. O painel informa quando a busca ainda precisa atualizar
  e mantém um aviso visível se não conseguir carregar os dados.

### Adicionado

- Arquivos de recuperação podem ser guardados pelo MCP com verificação de hash,
  descrição pesquisável e conteúdo separado da busca. O backup confere os originais.
- Leitura exata de fontes em lote, com continuação por caracteres e proteção contra
  mistura de versões durante uma recuperação.
- Diagnóstico agrupa falhas novas de leitura por uma impressão digital dos caminhos,
  sem guardar seus nomes ou o conteúdo das chamadas.

- Envio de imagens pelo MCP, inclusive pela CLI sem acesso ao painel, com
  conferência de integridade e reaproveitamento de imagens já guardadas.

- Diagnóstico separa espera por acesso aos dados, atualização do índice e consulta.
- Avaliação da busca com perguntas dos projetos e respostas esperadas, sem expor
  documentos no relatório.

- Registro de reconstruções lentas da busca nos logs do serviço, sem conteúdo privado.
- Relatório de uso com totais por projeto, evolução diária e operações que mais
  acumularam tempo, para orientar melhorias sem guardar o conteúdo das conversas.

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

- Instruções iniciais MCP mais curtas. O guia completo continua disponível na
  ferramenta `usage_guide`, sem mudar argumentos ou validações das ferramentas.

- Frentes aceitam o estado pausado. Use a mesma versão atualizada da OMM em todos
  os processos que acessam esses dados; versões antigas não consolidam suas revisões.
- A documentação de entrada e a organização do repositório explicam melhor por
  onde começar e como desenvolver a aplicação.
