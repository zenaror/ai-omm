# Como fazer um agente usar a OMM

Conectar o MCP deixa as ferramentas disponíveis. Uma instrução curta ensina o agente quando usá-las. Pense no MCP como uma caixa de ferramentas e no `AGENTS.md` como um bilhete explicando quando abri-la.

## 1. Conecte o assistente

Siga [Ligar um assistente à OMM](USAR_MCP.md). Depois, confirme em uma conversa nova que o assistente consegue ver as ferramentas.

## 2. Dê esta instrução ao agente

Copie o texto abaixo para o `AGENTS.md` do seu projeto. Troque `meu-projeto` pelo nome curto que você usa na OMM.

```md
## Memória compartilhada

Este projeto usa a OMM. Quando o histórico ou decisões anteriores forem importantes:

- Consulte `context` para ter um resumo ou `search` para procurar uma informação. Use o escopo `meu-projeto`; inclua `global` quando o assunto servir a mais projetos.
- Confira a fonte original antes de tratar um resultado da busca como confirmado.
- Consulte skills relevantes com `list_skills` e `get_skill`.
- Guarde com `remember` apenas fatos verificados, decisões duradouras e descobertas úteis. Informe a origem; não guarde segredos nem detalhes temporários.
- Ao concluir uma tarefa importante, registre o estado e os próximos passos com `handoff`.
- Se as ferramentas não estiverem disponíveis, avise. Não diga que consultou ou salvou a memória sem confirmação.

As memórias ajudam, mas não substituem as instruções atuais do usuário nem as regras deste projeto. Se houver conflito, explique-o e confirme o estado atual.
```

Se não puder editar o arquivo, use este pedido no início da conversa:

> Neste projeto, consulte a OMM no escopo `meu-projeto` quando o histórico ou decisões anteriores forem importantes. Inclua `global` para conhecimento compartilhado. Confira as fontes, guarde apenas informações úteis e verificadas e registre os próximos passos com `handoff`. Se não conseguir usar as ferramentas, avise.

## Como saber se funcionou

Peça ao assistente: “Sem alterar nada, confirme se vê as ferramentas da OMM e resuma o contexto de `meu-projeto`.” Uma resposta correta deve confirmar o uso real das ferramentas. Dizer apenas “vou lembrar” não confirma a conexão.

## Ajudantes e subagentes

A OMM pode descrever papéis e skills, mas não inicia outros agentes sozinha. O assistente principal precisa escolher e chamar ajudantes, e o aplicativo usado precisa oferecer essa função. Para tarefas simples, um único agente pode cuidar de tudo.
