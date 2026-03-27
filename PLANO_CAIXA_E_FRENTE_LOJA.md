# Plano Caixa, Frente de Loja e Contas a Receber

Este documento define a arquitetura recomendada para evoluir o ERP em dois modos operacionais:

- `PDV`
- `FRENTE_LOJA`

O objetivo e permitir que o mesmo sistema funcione tanto em operacao simples de venda com recebimento imediato quanto em operacao de caixa completo, sem retrabalho estrutural.

## 1. Conceito

Hoje o sistema esta muito proximo de dois cenarios diferentes:

### 1.1 Modo PDV

- a venda registra itens, cliente e pagamento
- o recebimento acontece na propria venda
- nao existe tela operacional de caixa
- fluxo rapido e direto

### 1.2 Modo Frente de Loja

- a venda registra itens, cliente e total
- o recebimento passa pelo caixa
- exige caixa aberto
- controla abertura, troco inicial, entradas, saidas, sangrias e fechamento
- gera rastreabilidade de operador e conferencias

## 2. Parametro de Operacao

Criar um parametro de configuracao por empresa:

- `modo_operacao`

Valores:

- `PDV`
- `FRENTE_LOJA`

Sugestao:

- default inicial: `PDV`

Comportamento:

- `PDV`: esconde menu de caixa e permite concluir venda com recebimento na tela
- `FRENTE_LOJA`: mostra menu de caixa e impede recebimento direto fora do caixa

## 3. Regras de Negocio

### 3.1 PDV

- venda pode registrar pagamentos normalmente
- se a condicao for a vista, o titulo ja nasce como pago
- se a condicao for parcelada ou a prazo, gera `contas_receber`
- nao exige caixa aberto

### 3.2 Frente de Loja

- venda nao recebe diretamente na tela de venda
- venda gera registro financeiro pendente
- para receber, precisa haver um caixa aberto
- recebimento entra como movimento de caixa
- troco precisa ser calculado e registrado
- formas nao numerarias tambem entram no caixa como movimento identificado

### 3.3 Fechamento de Caixa

- operador informa valor contado
- sistema calcula valor esperado
- sistema mostra diferenca
- fechamento precisa gravar data/hora, operador e observacao
- caixa fechado nao recebe mais movimento

### 3.4 Contas a Receber

- toda venda gera titulos financeiros
- em venda a vista no `PDV`, o titulo pode nascer como pago
- em `FRENTE_LOJA`, o titulo pode nascer pendente e ser baixado via caixa
- venda a prazo gera vencimentos e baixa parcial/total

## 4. Tabelas Recomendadas

### 4.1 Ajustes em `empresas`

Adicionar:

- `modo_operacao VARCHAR(20) DEFAULT 'PDV'`

### 4.2 Tabela `caixas`

Responsavel por representar uma sessao de caixa.

Campos sugeridos:

- `id`
- `empresa_id`
- `usuario_abertura_id`
- `usuario_fechamento_id`
- `data_abertura`
- `data_fechamento`
- `valor_inicial`
- `valor_esperado`
- `valor_informado_fechamento`
- `diferenca_fechamento`
- `status` (`ABERTO`, `FECHADO`, `CANCELADO`)
- `observacoes`
- `created_at`

### 4.3 Tabela `caixa_movimentos`

Responsavel pelo extrato do caixa.

Campos sugeridos:

- `id`
- `caixa_id`
- `usuario_id`
- `venda_id` nullable
- `conta_receber_id` nullable
- `tipo_movimento`
- `natureza` (`ENTRADA`, `SAIDA`)
- `forma_pagamento`
- `valor`
- `descricao`
- `troco`
- `created_at`

Tipos de movimento sugeridos:

- `ABERTURA`
- `RECEBIMENTO_VENDA`
- `RECEBIMENTO_CONTA`
- `SUPRIMENTO`
- `SANGRIA`
- `AJUSTE`
- `FECHAMENTO`

### 4.4 Tabela `contas_receber`

Responsavel pelos titulos financeiros de venda.

Campos sugeridos:

- `id`
- `venda_id`
- `pagamento_id` nullable
- `cliente_id`
- `numero_parcela`
- `descricao`
- `valor`
- `valor_recebido`
- `data_vencimento`
- `data_recebimento`
- `status`
- `created_at`

Status sugeridos:

- `PENDENTE`
- `PARCIAL`
- `PAGO`
- `ESTORNADO`

### 4.5 Tabela `financeiro_recebimentos_logs`

Auditoria de baixa e recebimentos.

Campos sugeridos:

- `id`
- `conta_receber_id`
- `venda_id`
- `caixa_id`
- `usuario_id`
- `acao`
- `descricao`
- `created_at`

### 4.6 Ajustes em `vendas`

Adicionar se necessario:

- `modo_operacao`
- `status_financeiro`
- `caixa_id` nullable

Status financeiros sugeridos:

- `PENDENTE`
- `PARCIAL`
- `PAGO`
- `ESTORNADO`

## 5. Permissoes de Usuario

Adicionar em `usuarios`:

- `permite_abrir_caixa`
- `permite_fechar_caixa`
- `permite_suprimento_caixa`
- `permite_sangria_caixa`
- `permite_receber_venda_caixa`
- `permite_baixar_contas_receber`
- `permite_ver_balancete`

Adicionar tambem telas controladas:

- `caixa`
- `contas_receber`
- `balancete`

## 6. Telas Recomendadas

### 6.1 Configuracao da Empresa

Na empresa:

- campo `Modo de operacao`
- opcoes `PDV` e `Frente de Loja`

### 6.2 Caixa

Layout sugerido:

- `Status do caixa`
- `Abertura`
- `Movimentos`
- `Suprimento / Sangria`
- `Fechamento`

Operacoes:

- abrir caixa
- registrar suprimento
- registrar sangria
- visualizar movimentos
- fechar caixa

### 6.3 Contas a Receber

Layout sugerido:

- `Busca`
- `Titulos cadastrados`
- `Baixa / Recebimento`

Filtros:

- cliente
- status
- vencimento inicial/final
- venda

### 6.4 Balancete

Layout sugerido:

- periodo
- resumo de entradas
- resumo de saidas
- saldo
- contas a pagar
- contas a receber
- caixa

## 7. Fluxo de Venda por Modo

### 7.1 Venda no modo PDV

1. usuario registra cliente e itens
2. usuario informa condicoes/pagamentos
3. sistema grava venda
4. sistema grava `vendas_pagamentos`
5. sistema grava `contas_receber`
6. se a vista, titulo ja fica pago
7. se parcelado, titulos ficam pendentes

### 7.2 Venda no modo Frente de Loja

1. usuario registra cliente e itens
2. venda e gravada sem recebimento direto
3. sistema gera `contas_receber`
4. operador vai ao caixa aberto
5. caixa recebe a venda
6. sistema grava `caixa_movimentos`
7. sistema baixa ou abate em `contas_receber`

## 8. Fluxo de Caixa

### 8.1 Abertura

- operador informa valor inicial
- sistema cria caixa `ABERTO`
- registra movimento `ABERTURA`

### 8.2 Recebimento de Venda

- caixa identifica venda ou titulo
- informa forma de pagamento
- informa valor recebido
- se houver troco, grava troco
- gera movimento `RECEBIMENTO_VENDA`
- baixa total ou parcial da conta

### 8.3 Sangria

- informa valor
- informa motivo
- grava `SAIDA`

### 8.4 Suprimento

- informa valor
- informa motivo
- grava `ENTRADA`

### 8.5 Fechamento

- sistema calcula saldo esperado
- operador informa valor contado
- sistema calcula diferenca
- caixa muda para `FECHADO`

## 9. Balancete

So faz sentido apos:

- `caixa`
- `contas_receber`
- `contas_pagar`

O balancete inicial pode ser um resumo por periodo com:

- recebimentos
- pagamentos
- saldo operacional
- valores em aberto
- totais por forma de pagamento

## 10. Ordem Recomendada de Implementacao

### Fase 1

- parametro `modo_operacao`
- novas permissoes
- novas telas no menu

### Fase 2

- tabelas `caixas` e `caixa_movimentos`
- abertura e fechamento de caixa
- suprimento e sangria

### Fase 3

- tabela `contas_receber`
- geracao de titulos nas vendas
- baixa manual

### Fase 4

- integracao venda x caixa
- diferenciar comportamento `PDV` x `FRENTE_LOJA`

### Fase 5

- balancete
- relatorios gerenciais

## 11. Compatibilidade com o que ja existe

Para nao quebrar o sistema atual:

- manter `PDV` como modo padrao
- manter a tela de venda atual funcionando no modo `PDV`
- introduzir `FRENTE_LOJA` apenas quando o caixa estiver pronto
- reaproveitar a logica financeira existente de `vendas_pagamentos` e vencimentos

## 12. Decisao Recomendada

Implementar primeiro:

1. `modo_operacao`
2. `caixa`
3. `contas_receber`
4. `integracao venda -> caixa/recebimento`
5. `balancete`

Isso permite evoluir o sistema sem refazer a base de vendas depois.
