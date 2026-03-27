# ERP Vendas

Sistema web para gestão comercial com foco em vendas, produtos, clientes, famílias, permissões de usuário, controle de estoque e relatórios.

## Versão

`v1.2a`

## Principais recursos

- Cadastro de produtos
- Cadastro de famílias de produtos
- Cadastro de clientes
- Cadastro de empresas
- Cadastro de usuários com permissões
- Cadastro de condições de pagamento
- Nova venda
- Listagem de vendas
- Estorno e exclusão de vendas com permissão
- Edição de vendas estornadas
- Dashboard comercial com filtros
- Relatório de estoque mínimo
- Histórico de movimentação de estoque por produto
- Inventário por ajuste de estoque no cadastro do produto
- Logo da empresa no sistema e no login
- Link de suporte via WhatsApp no login

## Tecnologias

- Python 3.10+
- Flask
- PostgreSQL
- psycopg2-binary
- Bootstrap 5

## Instalação

Clone o repositório:

```bash
git clone https://github.com/rmartolive/erp-vendas.git
cd erp-vendas
<<<<<<< HEAD
=======
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

## Configuração

Crie ou ajuste o arquivo `.env` com os dados do seu ambiente.

Exemplo:

```env
PGHOST=localhost
PGPORT=5432
PGDATABASE=seu_banco
PGUSER=postgres
PGPASSWORD=sua_senha
FLASK_SECRET_KEY=sua_chave_segura
```

Observação:

- `PGDATABASE` não precisa ser obrigatoriamente `erp_vendas`
- use o nome do banco que existir no seu ambiente

## Execução

```bash
python app_corrigido_permissoes.py
```

O sistema será iniciado em:

```txt
http://127.0.0.1:5000
```

## Estrutura principal

- `app_corrigido_permissoes.py`: aplicação principal
- `templates/`: telas HTML do sistema
- `requirements.txt`: dependências Python
- `.env`: configurações do ambiente
- `uploads/logos/`: logos das empresas

## Regras importantes do sistema

### Vendas

- Desconto geral não pode ser aplicado junto com desconto em item
- Venda ativa precisa ser estornada antes de editar
- Venda ativa precisa ser estornada antes de excluir

### Estoque

- Venda baixa estoque
- Estorno devolve estoque
- Alteração de estoque no produto gera histórico como inventário
- Histórico de estoque mostra usuário responsável pela movimentação

### Empresa

- A empresa pode definir campos obrigatórios para produtos e clientes
- A empresa pode bloquear desconto nas vendas
- A empresa pode permitir ou bloquear saldo negativo

## Permissões de usuário

O sistema possui permissões por usuário para:

- Vender abaixo do custo
- Definir desconto máximo
- Permitir login múltiplo
- Editar venda
- Estornar venda
- Excluir venda
- Fazer inventário

## Relatórios

- Dashboard comercial com filtros por cliente e período
- Resumo por categoria/família
- Ranking de produtos mais vendidos
- Relatório de estoque mínimo com sugestão de compra

## Suporte

WhatsApp:

[https://wa.me/5521980912630](https://wa.me/5521980912630)

## Release 1.2a

Melhorias desta versão:

- cadastro de famílias separado do cadastro de produtos
- edição de produtos e clientes
- reorganização das telas de produtos, clientes e vendas
- permissões por usuário para editar, estornar e excluir vendas
- dashboard comercial com filtros, KPIs, resumo por categoria e ranking de produtos
- relatório de estoque mínimo com sugestão de compra
- identidade visual da empresa com logo no menu e no login
- link de suporte via WhatsApp no login
- histórico de movimentação de estoque por produto com usuário responsável
- inventário tratado pela alteração de estoque no cadastro do produto

## Observações

Se o banco não permitir migrações automáticas, alguns `ALTER TABLE` podem precisar ser executados manualmente.
>>>>>>> 3a293c5 (Adiciona README da versao 1.2a)
