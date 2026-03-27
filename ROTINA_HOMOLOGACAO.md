# Rotina de Homologacao

Este documento define a rotina minima de homologacao antes de colocar o ERP em uso real.

## 1. Objetivo

Garantir que:

- o sistema sobe corretamente
- login e permissao funcionam
- estoque, vendas, compras e financeiro respeitam as regras do negocio
- relatorios e PDF geram corretamente
- uso em desktop e celular esta funcional

## 2. Ambiente

Executavel Python homologado:

```txt
C:\Users\Rb\AppData\Local\Python\pythoncore-3.14-64\python.exe
```

Dependencias:

```bash
"C:\Users\Rb\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m pip install -r requirements.txt
```

Compilacao basica:

```bash
"C:\Users\Rb\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m py_compile app_corrigido_permissoes.py
```

## 3. Dados Minimos para Homologacao

Criar ou garantir:

- 1 empresa com logo cadastrada
- 1 usuario `admin`
- 1 usuario `atendente`
- 2 familias de produto
- 3 produtos ativos
- 1 cliente
- 1 fornecedor
- 1 pessoa com tipo `AMBOS`
- 2 condicoes de pagamento de venda
- 2 condicoes de pagamento de compra

## 4. Regras de Aprovacao

Cada item homologado deve ter:

- resultado esperado atingido
- sem erro de tela ou traceback
- sem quebra visual grave no desktop
- sem quebra funcional no celular

Status sugeridos:

- `OK`
- `AJUSTAR`
- `BLOQUEANTE`

## 5. Checklist Geral

### 5.1 Inicializacao

- sistema abre sem erro
- login carrega corretamente
- reinicio do servidor exige novo login
- logout encerra a sessao
- usuario inativo nao entra

### 5.2 Responsividade

Testar no desktop e no celular:

- menu lateral abre e fecha
- menu hamburguer aparece no topo do celular
- telas principais carregam sem elementos sobrepostos
- botoes continuam clicaveis
- tabelas e formularios continuam utilizaveis

## 6. Homologacao por Modulo

### 6.1 Empresa

- cadastrar empresa
- editar empresa
- enviar logo
- validar exibicao da logo no sistema
- validar logo no PDF do relatorio

### 6.2 Usuarios e Permissoes

- cadastrar usuario
- editar usuario
- buscar usuario
- alterar telas visiveis
- validar permissao de acesso direto por URL
- validar login multiplo ligado
- validar login multiplo desligado

Permissoes para validar:

- editar venda
- estornar venda
- excluir venda
- inventario
- editar nota de entrada
- excluir nota de entrada
- estornar nota de entrada
- editar financeiro
- baixar contas a pagar

### 6.3 Pessoas

- cadastrar `CLIENTE`
- cadastrar `FORNECEDOR`
- cadastrar `AMBOS`
- editar cadastro
- filtrar por tipo
- validar uso do fornecedor na entrada de nota
- validar uso do cliente na venda

### 6.4 Familias e Produtos

- cadastrar familia
- cadastrar produto
- editar produto
- consultar historico de estoque
- validar estoque minimo
- validar custo e preco

### 6.5 Condicoes de Pagamento

- buscar condicao
- cadastrar condicao `VENDA`
- cadastrar condicao `COMPRA`
- cadastrar condicao `AMBOS`
- editar condicao na tabela
- validar uso correto nas vendas
- validar uso correto na entrada de nota

### 6.6 Vendas

- criar venda simples
- criar venda com mais de um item
- usar condicao de pagamento de venda
- validar geracao de vencimentos
- editar venda com permissao
- bloquear edicao sem permissao
- estornar venda com permissao
- bloquear estorno sem permissao
- excluir venda com permissao
- bloquear exclusao sem permissao
- validar movimentacao de estoque

### 6.7 Entrada de Nota

- criar nota em aberto
- criar nota finalizada
- editar nota em aberto
- editar nota finalizada sem movimentacao posterior
- bloquear edicao de nota finalizada com movimentacao posterior
- excluir nota em aberto
- excluir nota finalizada sem movimentacao posterior
- bloquear exclusao de nota finalizada com movimentacao posterior
- estornar nota finalizada
- bloquear estorno sem permissao
- validar atualizacao de estoque
- validar atualizacao de custo do produto

### 6.8 Financeiro / Contas a Pagar

- gerar faturas automaticas na entrada de nota
- editar faturas antes de salvar
- listar contas a pagar
- filtrar por fornecedor
- filtrar por status
- baixar titulo com permissao
- bloquear baixa sem permissao
- validar log em `financeiro_logs`
- validar estorno refletindo no financeiro

### 6.9 Relatorio de Estoque Minimo

- abrir relatorio
- buscar por produto
- validar totais do resumo
- validar sugestao de compra
- gerar PDF
- validar nome do arquivo
- validar logo da empresa no PDF
- validar marca d'agua `@rbcorp`

## 7. Cenarios Criticos de Negocio

Executar obrigatoriamente:

### Cenario A

- cadastrar fornecedor
- cadastrar produto sem estoque
- dar entrada de nota finalizada
- validar aumento de estoque
- validar contas a pagar

### Cenario B

- apos entrada finalizada, movimentar o mesmo produto em outra operacao
- tentar editar a nota
- tentar excluir a nota
- sistema deve bloquear

### Cenario C

- criar nota finalizada
- sem outra movimentacao depois
- editar a nota
- sistema deve permitir
- validar estoque final correto

### Cenario D

- baixar uma conta a pagar
- validar status pago
- validar data de pagamento
- validar log

### Cenario E

- reiniciar o servidor
- abrir sistema novamente
- usuario deve ir para login

## 8. Evidencias

Salvar evidencias de:

- tela funcionando
- PDF gerado
- logs financeiros
- bloqueios de permissao
- bloqueios por regra de estoque

Nome sugerido para evidencias:

```txt
AAAA-MM-DD_modulo_cenario_resultado
```

Exemplo:

```txt
2026-03-27_entrada-nota_cenario-b_ok
```

## 9. Go-Live

Liberar uso real apenas se:

- nenhum item bloqueante permanecer aberto
- PDF gerar corretamente
- login e sessao estiverem validados
- estoque e financeiro baterem com os testes
- backup do banco estiver garantido

## 10. Resultado Final

Ao final da homologacao, registrar:

- data
- responsavel
- itens aprovados
- itens pendentes
- itens bloqueantes
- decisao final: `APROVADO`, `APROVADO COM RESSALVAS`, `NAO APROVADO`
