# Orientações para mudanças na OMM

## Entrada do projeto

- Este repositório mantém a aplicação One Mind Machine. As notas, fontes e skills dos usuários ficam na pasta de dados configurada, separada do código.
- Quando houver conexão com a OMM, consulte o escopo `ai-omm` e a base comum com `get_role(name="project-base")`. Abra apenas as regras e skills necessárias; se a conexão faltar, informe a limitação e use as fontes deste repositório.
- O mapa de arquivos e os comandos essenciais estão no README, em "Organização para desenvolver". Preserve os caminhos existentes; não crie cópias concorrentes de documentação ou configuração obrigatória de um cliente de IA.
- Antes de editar, confira branch, HEAD e alterações locais. Ao fechar trabalho relevante, preserve evidências e atualize o handoff por SHA quando a OMM estiver disponível. Commit e push dependem da autorização da tarefa.
- Cleanup só remove artefatos conhecidos e descartáveis da tarefa. Preserve a pasta de dados, originais, resultados únicos e trabalho de outras sessões; um arquivo ignorado pelo Git não é automaticamente descartável.

## Escrever para quem usa a OMM

- Toda explicação destinada à pessoa que usa a OMM deve ser ELI5: simples o bastante para alguém sem experiência técnica conseguir começar.
- Escreva em português do Brasil, com frases curtas e palavras comuns. Prefira explicar o que a pessoa verá ou fará a descrever detalhes internos do programa.
- Se um termo técnico ou uma sigla for necessário, explique na primeira vez com uma frase simples. Não presuma que a pessoa saiba o que são MCP, Git, container, escopo ou índice.
- Em instruções passo a passo, diga onde fazer cada coisa, o que copiar, qual resultado esperar e como reconhecer ou corrigir um erro comum.
- Diferencie o que é obrigatório do que é opcional. Não esconda uma limitação que muda o que a pessoa precisa fazer.
- Use exemplos prontos que possam ser copiados e troque os valores de exemplo por nomes fáceis de reconhecer.
- Deixe detalhes avançados em uma seção separada, depois do caminho simples para começar.
- Aplique a mesma regra às telas, mensagens de erro, README, guias e exemplos de prompts ou arquivos de instrução para agentes.

O código e os nomes técnicos usados por ferramentas podem continuar em inglês quando isso for necessário para funcionar. Explique esses nomes ao apresentá-los à pessoa usuária.
