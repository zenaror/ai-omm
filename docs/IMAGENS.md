# Guardar imagens como evidência

Uma captura ajuda a mostrar um resultado. A descrição explica **de onde ela veio,
o que foi testado e o que ainda não foi comprovado**. A OMM guarda as duas juntas.
Isso vale para qualquer projeto, não apenas jogos ou emuladores.

## Pelo painel

1. Abra **Evidências em imagens**, no fim do painel.
2. Informe o projeto, um título, o que a imagem mostra e sua origem.
3. Escolha o tipo: aparelho real, emulador, análise estática, teste sintético ou outro.
4. Selecione os arquivos e clique em **Guardar imagens**.
5. Confira o resultado de cada arquivo. A galeria permite abrir o original.

Use as limitações para evitar conclusões maiores que a evidência: uma captura
de emulador não comprova funcionamento em aparelho real. Data, build e commit
são opcionais; informe somente o que souber. Não invente a data da captura.

O painel aceita até 20 imagens por envio, com descrição compartilhada e nome do
arquivo acrescentado ao título. Para descrever cada imagem de maneira diferente,
use envios separados ou o manifesto abaixo. Não envie automaticamente todas as
capturas de uma pasta temporária: selecione as que sustentam uma conclusão.

## O que é preservado

- PNG, JPEG e WebP estáticos, até 10 MiB, 16 milhões de pixels e 8.192 pixels por lado.
- O original é guardado **sem recompressão**, com uma impressão digital SHA-256.
- Uma descrição Markdown acompanha o arquivo e entra na busca por palavras e por assunto.
- A prévia tem no máximo 512 pixels por lado. Ela não substitui o original.
- Não há OCR (leitura automática do texto da imagem), interpretação visual automática
  nem busca pelo conteúdo dos pixels. Quem registrou a descrição responde pelo relato.

Confira dados pessoais antes de enviar. O original preserva também metadados
embutidos, como EXIF; só a prévia remove esses metadados. A OMM não identifica
automaticamente dados pessoais dentro da imagem.

O mesmo arquivo, com a mesma descrição, não cria duplicata no projeto. Descrições
ou origens diferentes criam registros separados e podem compartilhar o original.
A deduplicação compara bytes, não a aparência das imagens.

## Para assistentes

Primeiro procure a descrição com `search_sources` ou `list_image_evidence`.
Só use `read_image_evidence` quando os pixels forem necessários. O padrão devolve
a prévia; peça `variant="original"` para examinar detalhes. Clientes sem suporte
visual ainda podem usar as descrições e o painel.

O envio usa o painel, a CLI ou HTTP, sem colocar a imagem em texto na conversa.
O relatório `usage_report` conta o texto acompanhante, **não o custo visual nem
tokens cobrados pelo provedor**. Consultar só descrições evita enviar imagens sem necessidade.

## Enviar pelo terminal (opcional)

Na máquina que tem o arquivo, instale a versão atual da OMM. Crie `captura.json`:

```json
{
  "title": "Tela após abrir as configurações",
  "description": "O menu apareceu e o cursor está na primeira opção.",
  "origin": "Captura do emulador após iniciar a build de teste.",
  "evidence_type": "emulator",
  "limitations": "Ainda não conferido em aparelho real."
}
```

Com `OMM_MCP_TOKEN` já configurado no ambiente, envie para o endereço **do painel**,
normalmente na porta 8001. O token não entra no arquivo JSON:

```sh
omm image-import --scope meu-projeto --file captura.png --metadata captura.json \
  --server-url http://servidor-omm:8001
```

No próprio host, com acesso ao diretório canônico, omita `--server-url` e use
`omm --root /data image-import ...`. Não execute isso sobre um espelho desatualizado.

Para um lote, crie `lote.json`. Os caminhos são relativos à pasta do manifesto:

```json
[
  {
    "file": "capturas/tela-01.png",
    "metadata": {
      "title": "Primeira tela",
      "description": "Resultado observado neste caso específico.",
      "origin": "Teste A, passo 3, captura do emulador.",
      "evidence_type": "emulator"
    }
  }
]
```

```sh
omm image-import-batch --scope meu-projeto --manifest lote.json \
  --server-url http://servidor-omm:8001
```

São até 50 itens por manifesto. Cada resultado tem seu índice. Se houver falha,
o comando sai com código 2 e `status: partial`; os itens bem-sucedidos ficam
guardados. Corrija a causa e repita: os itens idênticos não serão duplicados.

Campos opcionais de `metadata`: `limitations`, `captured_at` (data com fuso,
como `2026-10-26T10:00:00Z`), `repository` (`equipe/projeto`), `git_revision`
(hash completo de 40 ou 64 caracteres) e `build`. Não inclua campos desconhecidos.

Integrações HTTP usam `POST /api/images` no painel, com autenticação Bearer pelo
token MCP e formulário multipart de três campos: `scope`, `metadata` (JSON) e
`file` (bytes). A CLI não segue redirecionamentos desse envio. Essa autenticação
vale só para as rotas de imagens, não libera outras ações administrativas.

## Backup e remoção

**Antes do primeiro envio, atualize tanto a aplicação quanto o serviço de backup
para esta versão.** O validador antigo não aceita os novos arquivos de imagem.
Não é necessário criar outro volume. Os originais e as descrições ficam em
`sources/<projeto>/evidence/` e entram no mecanismo de backup Git já configurado.
“Guardado na OMM” não significa “já enviado ao backup remoto”.

O limite padrão é 256 MiB de imagens atuais por projeto. A variável opcional
`OMM_IMAGE_SCOPE_QUOTA_MIB` muda esse limite. Ele não limita o histórico Git;
coleções grandes podem aumentar bastante o tamanho do backup.

Para remover, use o painel ou `delete_image_evidence`, passando o SHA-256 atual
da **descrição**, obtido na listagem. A remoção preserva um original ainda usado
por outra descrição na mesma pasta. Referências em handoffs não são apagadas
automaticamente. Versões antigas podem continuar no histórico Git.

As descrições de imagens não podem ser alteradas pelas ferramentas de texto.
Para corrigir uma, importe o original com a descrição certa, ajuste os ponteiros
necessários e remova a descrição antiga quando apropriado.
