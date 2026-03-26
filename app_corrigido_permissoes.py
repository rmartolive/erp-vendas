import os
import hashlib
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)
import psycopg2
from psycopg2.extras import RealDictCursor


# =========================================================
# ENV
# =========================================================
BASE_DIR = Path(__file__).resolve().parent


def load_env_file(env_path: str | Path = ".env") -> bool:
    path = Path(env_path)
    if not path.exists():
        return False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")
    return True


# Tenta carregar .env primeiro da pasta do projeto e depois do diretório atual.
# Assim funciona tanto ao rodar pelo terminal dentro da pasta quanto por atalho/IDE.
load_env_file(BASE_DIR / ".env")
load_env_file(Path.cwd() / ".env")


# =========================================================
# APP
# =========================================================
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "troque_essa_chave_em_producao")


# =========================================================
# DB
# =========================================================
def get_conn():
    database_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if database_url:
        return psycopg2.connect(database_url, cursor_factory=RealDictCursor)

    host = os.getenv("PGHOST") or os.getenv("DB_HOST") or "localhost"
    port = int(os.getenv("PGPORT") or os.getenv("DB_PORT") or "5432")
    dbname = os.getenv("PGDATABASE") or os.getenv("DB_NAME") or "erp_vendas"
    user = os.getenv("PGUSER") or os.getenv("DB_USER") or "postgres"
    password = os.getenv("PGPASSWORD") or os.getenv("DB_PASSWORD") or "postgres"

    return psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        cursor_factory=RealDictCursor,
    )


def fetch_all(query, params=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def fetch_one(query, params=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def execute(query, params=None, returning=False):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            data = dict(cur.fetchone()) if returning else None
        conn.commit()
        return data
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def parse_decimal(value, default=0.0):
    try:
        if value is None:
            return float(default)
        text = str(value).strip().replace(".", "").replace(",", ".")
        if text == "":
            return float(default)
        return float(text)
    except Exception:
        return float(default)


def get_familias_produtos(apenas_ativas=False):
    query = """
        SELECT id, nome, ativo, created_at
        FROM public.familias_produtos
    """

    if apenas_ativas:
        query += " WHERE ativo = TRUE"

    query += " ORDER BY nome"
    return fetch_all(query)


# =========================================================
# AUTH / SCHEMA
# =========================================================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()
def parse_quantity_by_unidade(unidade, value):
    unidade = (unidade or "").strip().lower()
    texto = str(value or "").strip().replace(",", ".")
    if texto == "":
        return 0

    if unidade in ("un", "und", "unidade", "unidades"):
        try:
            return int(float(texto))
        except Exception:
            return 0

    try:
        return float(texto)
    except Exception:
        return 0.0


def unidade_permite_fracao(unidade):
    unidade = (unidade or "").strip().lower()
    return unidade in ("m", "mt", "mts", "metro", "metros")






def column_exists(table_name, column_name, schema="public"):
    row = fetch_one(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
        ) AS exists
        """,
        (schema, table_name, column_name),
    )
    return bool(row["exists"]) if row else False

def ensure_schema():
    ddl_statements = [
        """
        CREATE TABLE IF NOT EXISTS public.usuarios (
            id BIGSERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            perfil VARCHAR(20) NOT NULL CHECK (perfil IN ('admin', 'atendente')),
            ativo BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS public.pessoas (
            id BIGSERIAL PRIMARY KEY,
            tipo VARCHAR(20) DEFAULT 'cliente',
            nome TEXT NOT NULL,
            documento TEXT,
            telefone TEXT,
            email TEXT,
            cidade TEXT,
            estado VARCHAR(2),
            ativo BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE public.pessoas
            ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) DEFAULT 'cliente',
            ADD COLUMN IF NOT EXISTS documento TEXT,
            ADD COLUMN IF NOT EXISTS telefone TEXT,
            ADD COLUMN IF NOT EXISTS email TEXT,
            ADD COLUMN IF NOT EXISTS cidade TEXT,
            ADD COLUMN IF NOT EXISTS estado VARCHAR(2),
            ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        """,
        """
        CREATE TABLE IF NOT EXISTS public.produtos (
            id BIGSERIAL PRIMARY KEY,
            codigo TEXT,
            descricao TEXT NOT NULL,
            categoria TEXT,
            unidade VARCHAR(10),
            custo NUMERIC(14,2) DEFAULT 0,
            preco_venda NUMERIC(14,2) DEFAULT 0,
            estoque_atual NUMERIC(14,2) DEFAULT 0,
            estoque_minimo NUMERIC(14,2) DEFAULT 0,
            ativo BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS public.familias_produtos (
            id BIGSERIAL PRIMARY KEY,
            nome TEXT NOT NULL UNIQUE,
            ativo BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE public.familias_produtos
            ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        """,
        """
        ALTER TABLE public.produtos
            ADD COLUMN IF NOT EXISTS codigo TEXT,
            ADD COLUMN IF NOT EXISTS categoria TEXT,
            ADD COLUMN IF NOT EXISTS familia_id BIGINT,
            ADD COLUMN IF NOT EXISTS unidade VARCHAR(10),
            ADD COLUMN IF NOT EXISTS custo NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS preco_venda NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS estoque_atual NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS estoque_minimo NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        """,
        """
        CREATE TABLE IF NOT EXISTS public.empresas (
            id BIGSERIAL PRIMARY KEY,
            nome_fantasia TEXT NOT NULL,
            razao_social TEXT,
            cnpj VARCHAR(30),
            telefone VARCHAR(30),
            email TEXT,
            cidade TEXT,
            estado VARCHAR(2),
            responsavel TEXT,
            permite_estoque_negativo BOOLEAN DEFAULT FALSE,
            ativo BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE public.empresas
            ADD COLUMN IF NOT EXISTS razao_social TEXT,
            ADD COLUMN IF NOT EXISTS cnpj VARCHAR(30),
            ADD COLUMN IF NOT EXISTS telefone VARCHAR(30),
            ADD COLUMN IF NOT EXISTS email TEXT,
            ADD COLUMN IF NOT EXISTS cidade TEXT,
            ADD COLUMN IF NOT EXISTS estado VARCHAR(2),
            ADD COLUMN IF NOT EXISTS responsavel TEXT,
            ADD COLUMN IF NOT EXISTS permite_estoque_negativo BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        """,
        """
        CREATE TABLE IF NOT EXISTS public.condicoes_pagamento (
            id BIGSERIAL PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            forma_pagamento VARCHAR(30),
            parcelas INTEGER NOT NULL DEFAULT 1,
            dias_intervalo INTEGER NOT NULL DEFAULT 30,
            taxa_percentual NUMERIC(14,2) NOT NULL DEFAULT 0,
            ativo BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE public.condicoes_pagamento
            ADD COLUMN IF NOT EXISTS forma_pagamento VARCHAR(30),
            ADD COLUMN IF NOT EXISTS parcelas INTEGER DEFAULT 1,
            ADD COLUMN IF NOT EXISTS dias_intervalo INTEGER DEFAULT 30,
            ADD COLUMN IF NOT EXISTS taxa_percentual NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        """,
        """
        CREATE TABLE IF NOT EXISTS public.vendas (
            id BIGSERIAL PRIMARY KEY,
            cliente_id BIGINT REFERENCES public.pessoas(id),
            usuario_id BIGINT REFERENCES public.usuarios(id),
            data_venda TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            valor_bruto NUMERIC(14,2) DEFAULT 0,
            desconto NUMERIC(14,2) DEFAULT 0,
            acrescimo NUMERIC(14,2) DEFAULT 0,
            valor_liquido NUMERIC(14,2) DEFAULT 0,
            forma_pagamento VARCHAR(30),
            parcelas INTEGER DEFAULT 1,
            valor_parcela NUMERIC(14,2) DEFAULT 0,
            condicao_pagamento_id BIGINT,
            observacoes TEXT,
            condicoes_pagamento_ids TEXT
        )
        """,
        """
        ALTER TABLE public.vendas
            ADD COLUMN IF NOT EXISTS data_venda TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            ADD COLUMN IF NOT EXISTS valor_bruto NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS desconto NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS acrescimo NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS valor_liquido NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS forma_pagamento VARCHAR(30),
            ADD COLUMN IF NOT EXISTS parcelas INTEGER DEFAULT 1,
            ADD COLUMN IF NOT EXISTS valor_parcela NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS condicao_pagamento_id BIGINT,
            ADD COLUMN IF NOT EXISTS observacoes TEXT,
            ADD COLUMN IF NOT EXISTS condicoes_pagamento_ids TEXT
        """,
        """
        CREATE TABLE IF NOT EXISTS public.venda_itens (
            id BIGSERIAL PRIMARY KEY,
            venda_id BIGINT NOT NULL REFERENCES public.vendas(id) ON DELETE CASCADE,
            produto_id BIGINT NOT NULL REFERENCES public.produtos(id),
            quantidade NUMERIC(14,3) NOT NULL DEFAULT 0,
            valor_unitario NUMERIC(14,2) NOT NULL DEFAULT 0,
            total NUMERIC(14,2) NOT NULL DEFAULT 0
        )
        """,
        """
        ALTER TABLE public.venda_itens
            ADD COLUMN IF NOT EXISTS desconto_tipo VARCHAR(20) DEFAULT 'valor',
            ADD COLUMN IF NOT EXISTS desconto_valor NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS desconto_total NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS total_bruto NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS total_liquido NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS observacao_estoque TEXT
        """,
        """
        ALTER TABLE public.vendas
            ADD COLUMN IF NOT EXISTS desconto_tipo VARCHAR(20) DEFAULT 'valor',
            ADD COLUMN IF NOT EXISTS desconto_itens_total NUMERIC(14,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS desconto_geral_valor NUMERIC(14,2) DEFAULT 0
        """,
        """
        CREATE TABLE IF NOT EXISTS public.vendas_pagamentos (
            id BIGSERIAL PRIMARY KEY,
            venda_id BIGINT NOT NULL REFERENCES public.vendas(id) ON DELETE CASCADE,
            condicao_pagamento_id BIGINT REFERENCES public.condicoes_pagamento(id),
            descricao_condicao TEXT,
            forma_pagamento VARCHAR(30),
            valor NUMERIC(14,2) NOT NULL DEFAULT 0,
            parcelas INTEGER NOT NULL DEFAULT 1,
            dias_intervalo INTEGER NOT NULL DEFAULT 30,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS public.vendas_vencimentos (
            id BIGSERIAL PRIMARY KEY,
            venda_id BIGINT NOT NULL REFERENCES public.vendas(id) ON DELETE CASCADE,
            pagamento_id BIGINT REFERENCES public.vendas_pagamentos(id) ON DELETE CASCADE,
            numero_parcela INTEGER NOT NULL,
            valor NUMERIC(14,2) NOT NULL,
            data_vencimento DATE NOT NULL,
            data_pagamento DATE,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDENTE',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE public.vendas_vencimentos
            ADD COLUMN IF NOT EXISTS pagamento_id BIGINT
        """,
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'vendas'
                  AND column_name = 'condicao_pagamento'
            ) THEN
                ALTER TABLE public.vendas DROP COLUMN condicao_pagamento;
            END IF;
        END $$;
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name = 'produtos'
                  AND constraint_name = 'fk_produtos_familia'
            ) THEN
                ALTER TABLE public.produtos
                ADD CONSTRAINT fk_produtos_familia
                FOREIGN KEY (familia_id)
                REFERENCES public.familias_produtos(id);
            END IF;
        END $$;
        """,
        """
        INSERT INTO public.familias_produtos (nome)
        SELECT DISTINCT BTRIM(categoria)
        FROM public.produtos
        WHERE NULLIF(BTRIM(COALESCE(categoria, '')), '') IS NOT NULL
        ON CONFLICT (nome) DO NOTHING
        """,
        """
        UPDATE public.produtos p
           SET familia_id = f.id
          FROM public.familias_produtos f
         WHERE p.familia_id IS NULL
           AND NULLIF(BTRIM(COALESCE(p.categoria, '')), '') IS NOT NULL
           AND BTRIM(p.categoria) = f.nome
        """,
        """
        UPDATE public.produtos p
           SET categoria = f.nome
          FROM public.familias_produtos f
         WHERE p.familia_id = f.id
           AND COALESCE(p.categoria, '') <> f.nome
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name = 'vendas'
                  AND constraint_name = 'fk_vendas_condicao_pagamento'
            ) THEN
                ALTER TABLE public.vendas
                ADD CONSTRAINT fk_vendas_condicao_pagamento
                FOREIGN KEY (condicao_pagamento_id)
                REFERENCES public.condicoes_pagamento(id);
            END IF;
        END $$;
        """,
    ]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for stmt in ddl_statements:
                try:
                    cur.execute(stmt)
                    conn.commit()
                except psycopg2.errors.InsufficientPrivilege:
                    conn.rollback()
                    print("[ensure_schema] Sem permissão para alterar uma tabela existente. Vou seguir sem aplicar essa migração automática.")
                except psycopg2.Error:
                    conn.rollback()
                    raise

            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'usuarios'
                ) AS existe
                """
            )
            if cur.fetchone()["existe"]:
                cur.execute("SELECT COUNT(*) AS total FROM public.usuarios")
                total = cur.fetchone()["total"]
                if total == 0:
                    cur.execute(
                        """
                        INSERT INTO public.usuarios (nome, username, password_hash, perfil, ativo)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        ("Administrador", "admin", hashlib.sha256("123456".encode()).hexdigest(), "admin", True),
                    )
                    conn.commit()
    finally:
        conn.close()

# =========================================================
# DECORATORS
# =========================================================
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("perfil") != "admin":
            flash("Acesso permitido apenas para administrador.", "danger")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)

    return wrapper


# =========================================================
# CONTEXT
# =========================================================
@app.context_processor
def inject_user():
    return {
        "session_user_nome": session.get("nome"),
        "session_user_perfil": session.get("perfil"),
    }


# =========================================================
# AUTH ROUTES
# =========================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = fetch_one(
            """
            SELECT id, nome, username, perfil, ativo, password_hash
            FROM public.usuarios
            WHERE username = %s
            """,
            (username,),
        )

        if not user:
            flash("Usuário não encontrado.", "danger")
            return render_template("login.html")

        if not user["ativo"]:
            flash("Usuário inativo.", "danger")
            return render_template("login.html")

        if user["password_hash"] != hash_password(password):
            flash("Senha inválida.", "danger")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["nome"] = user["nome"]
        session["username"] = user["username"]
        session["perfil"] = user["perfil"]

        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================
# DASHBOARD
# =========================================================
@app.route("/")
@login_required
def dashboard():
    total_clientes = fetch_one("SELECT COUNT(*) AS total FROM public.pessoas")
    total_produtos = fetch_one("SELECT COUNT(*) AS total FROM public.produtos")
    total_usuarios = fetch_one("SELECT COUNT(*) AS total FROM public.usuarios")
    estoque = fetch_one(
        "SELECT COALESCE(SUM(estoque_atual), 0) AS total FROM public.produtos"
    )

    ultimos_clientes = fetch_all(
        """
        SELECT id, tipo, nome, documento, telefone, email, ativo, created_at
        FROM public.pessoas
        ORDER BY id DESC
        LIMIT 10
        """
    )

    return render_template(
        "dashboard.html",
        total_clientes=total_clientes["total"] if total_clientes else 0,
        total_produtos=total_produtos["total"] if total_produtos else 0,
        total_usuarios=total_usuarios["total"] if total_usuarios else 0,
        estoque_total=estoque["total"] if estoque else 0,
        ultimos_clientes=ultimos_clientes,
    )


# =========================================================
# USUÁRIOS
# =========================================================
@app.route("/usuarios")
@admin_required
def usuarios():
    rows = fetch_all(
        """
        SELECT id, nome, username, perfil, ativo, created_at
        FROM public.usuarios
        ORDER BY id DESC
        """
    )
    return render_template("usuarios.html", usuarios=rows)


@app.route("/usuarios/novo", methods=["POST"])
@admin_required
def usuarios_novo():
    nome = request.form.get("nome", "").strip()
    username = request.form.get("username", "").strip()
    senha = request.form.get("senha", "")
    perfil = request.form.get("perfil", "atendente")
    ativo = "ativo" in request.form

    if not nome or not username or not senha:
        flash("Preencha nome, usuário e senha.", "warning")
        return redirect(url_for("usuarios"))

    try:
        execute(
            """
            INSERT INTO public.usuarios
            (nome, username, password_hash, perfil, ativo)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (nome, username, hash_password(senha), perfil, ativo),
        )
        flash("Usuário cadastrado com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar usuário: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("usuarios"))


# =========================================================
# PESSOAS / CLIENTES
# =========================================================
@app.route("/pessoas")
@login_required
def pessoas():
    termo = request.args.get("q", "").strip()

    if termo:
        rows = fetch_all(
            """
            SELECT id, tipo, nome, documento, telefone, email, cidade, estado, ativo, created_at
            FROM public.pessoas
            WHERE nome ILIKE %s
               OR documento ILIKE %s
               OR telefone ILIKE %s
               OR email ILIKE %s
            ORDER BY id DESC
            """,
            (f"%{termo}%", f"%{termo}%", f"%{termo}%", f"%{termo}%"),
        )
    else:
        rows = fetch_all(
            """
            SELECT id, tipo, nome, documento, telefone, email, cidade, estado, ativo, created_at
            FROM public.pessoas
            ORDER BY id DESC
            """
        )

    return render_template("pessoas.html", pessoas=rows, termo=termo)


@app.route("/pessoas/novo", methods=["POST"])
@login_required
def pessoas_novo():
    tipo = request.form.get("tipo", "").strip()
    nome = request.form.get("nome", "").strip()
    documento = request.form.get("documento", "").strip()
    telefone = request.form.get("telefone", "").strip()
    email = request.form.get("email", "").strip()
    endereco = request.form.get("endereco", "").strip()
    cidade = request.form.get("cidade", "").strip()
    estado = request.form.get("estado", "").strip().upper()
    cep = request.form.get("cep", "").strip()
    observacoes = request.form.get("observacoes", "").strip()
    ativo = "ativo" in request.form

    if not nome:
        flash("O nome é obrigatório.", "warning")
        return redirect(url_for("pessoas"))

    try:
        execute(
            """
            INSERT INTO public.pessoas (
                tipo, nome, documento, telefone, email,
                endereco, cidade, estado, cep, observacoes, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tipo, nome, documento, telefone, email,
                endereco, cidade, estado, cep, observacoes, ativo
            ),
        )
        flash("Cliente cadastrado com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar cliente: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("pessoas"))


# =========================================================
# PRODUTOS
# =========================================================
@app.route("/produtos")
@login_required
def produtos():
    termo = request.args.get("q", "").strip()

    if termo:
        rows = fetch_all(
            """
            SELECT
                id, codigo, descricao, categoria, unidade,
                custo, preco_venda, estoque_atual, estoque_minimo,
                ativo, created_at
            FROM public.produtos
            WHERE descricao ILIKE %s
               OR COALESCE(codigo, '') ILIKE %s
               OR COALESCE(categoria, '') ILIKE %s
            ORDER BY id DESC
            """,
            (f"%{termo}%", f"%{termo}%", f"%{termo}%"),
        )
    else:
        rows = fetch_all(
            """
            SELECT
                id, codigo, descricao, categoria, unidade,
                custo, preco_venda, estoque_atual, estoque_minimo,
                ativo, created_at
            FROM public.produtos
            ORDER BY id DESC
            """
        )

    return render_template("produtos.html", produtos=rows, termo=termo)


@app.route("/produtos/novo", methods=["POST"])
@login_required
def produtos_novo():
    codigo = request.form.get("codigo", "").strip()
    descricao = request.form.get("descricao", "").strip()
    categoria = request.form.get("categoria", "").strip()
    unidade = request.form.get("unidade", "").strip()
    custo = request.form.get("custo", "0").strip() or "0"
    preco_venda = request.form.get("preco_venda", "0").strip() or "0"
    estoque_atual = request.form.get("estoque_atual", "0").strip() or "0"
    estoque_minimo = request.form.get("estoque_minimo", "0").strip() or "0"
    ativo = "ativo" in request.form

    if not descricao:
        flash("A descrição do produto é obrigatória.", "warning")
        return redirect(url_for("produtos"))

    try:
        execute(
            """
            INSERT INTO public.produtos (
                codigo, descricao, categoria, unidade,
                custo, preco_venda, estoque_atual, estoque_minimo, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                codigo, descricao, categoria, unidade,
                custo, preco_venda, estoque_atual, estoque_minimo, ativo
            ),
        )
        flash("Produto cadastrado com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar produto: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("produtos"))


# =========================================================
# CONDIÇÕES DE PAGAMENTO
# =========================================================
@app.route("/condicoes-pagamento")
@login_required
def condicoes_pagamento():
    rows = fetch_all(
        """
        SELECT
            id, nome, forma_pagamento, parcelas,
            dias_intervalo, taxa_percentual, ativo, created_at
        FROM public.condicoes_pagamento
        ORDER BY nome
        """
    )
    return render_template("condicoes_pagamento.html", condicoes=rows)


@app.route("/condicoes-pagamento/nova", methods=["POST"])
@login_required
def condicoes_pagamento_nova():
    nome = request.form.get("nome", "").strip()
    forma_pagamento = request.form.get("forma_pagamento", "").strip()
    parcelas = int(request.form.get("parcelas", "1") or 1)
    dias_intervalo = int(request.form.get("dias_intervalo", "30") or 30)
    taxa_percentual = request.form.get("taxa_percentual", "0").strip() or "0"
    ativo = "ativo" in request.form

    if not nome or not forma_pagamento:
        flash("Informe nome e forma de pagamento.", "warning")
        return redirect(url_for("condicoes_pagamento"))

    if parcelas < 1:
        parcelas = 1

    if dias_intervalo < 0:
        dias_intervalo = 0

    try:
        execute(
            """
            INSERT INTO public.condicoes_pagamento
            (nome, forma_pagamento, parcelas, dias_intervalo, taxa_percentual, ativo)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (nome, forma_pagamento, parcelas, dias_intervalo, taxa_percentual, ativo),
        )
        flash("Condição de pagamento cadastrada com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar condição: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("condicoes_pagamento"))


@app.route("/condicoes-pagamento/<int:condicao_id>/editar", methods=["POST"])
@login_required
def condicoes_pagamento_editar(condicao_id):
    nome = request.form.get("nome", "").strip()
    forma_pagamento = request.form.get("forma_pagamento", "").strip()
    parcelas = int(request.form.get("parcelas", "1") or 1)
    dias_intervalo = int(request.form.get("dias_intervalo", "30") or 30)
    taxa_percentual = request.form.get("taxa_percentual", "0").strip() or "0"
    ativo = "ativo" in request.form

    if not nome or not forma_pagamento:
        flash("Informe nome e forma de pagamento.", "warning")
        return redirect(url_for("condicoes_pagamento"))

    if parcelas < 1:
        parcelas = 1

    if dias_intervalo < 0:
        dias_intervalo = 0

    try:
        execute(
            """
            UPDATE public.condicoes_pagamento
               SET nome = %s,
                   forma_pagamento = %s,
                   parcelas = %s,
                   dias_intervalo = %s,
                   taxa_percentual = %s,
                   ativo = %s
             WHERE id = %s
            """,
            (
                nome,
                forma_pagamento,
                parcelas,
                dias_intervalo,
                taxa_percentual,
                ativo,
                condicao_id,
            ),
        )
        flash("Condição de pagamento atualizada com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao atualizar condição: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("condicoes_pagamento"))


# =========================================================
# VENDAS
# =========================================================
@app.route("/vendas")
@login_required
def vendas():
    rows = fetch_all(
        """
        SELECT
            v.id,
            v.data_venda,
            v.valor_bruto,
            v.desconto,
            v.acrescimo,
            v.valor_liquido,
            v.forma_pagamento,
            v.parcelas,
            v.valor_parcela,
            p.nome AS cliente,
            u.nome AS usuario,
            cp.nome AS condicao_pagamento
        FROM public.vendas v
        LEFT JOIN public.pessoas p ON p.id = v.cliente_id
        LEFT JOIN public.usuarios u ON u.id = v.usuario_id
        LEFT JOIN public.condicoes_pagamento cp ON cp.id = v.condicao_pagamento_id
        ORDER BY v.id DESC
        """
    )
    return render_template("vendas.html", vendas=rows)


@app.route("/vendas/nova", methods=["GET", "POST"])
@login_required
def vendas_nova():
    clientes = fetch_all(
        """
        SELECT id, nome
        FROM public.pessoas
        WHERE ativo = TRUE
        ORDER BY nome
        """
    )

    produtos = fetch_all(
        """
        SELECT id, descricao, unidade, preco_venda, estoque_atual, ativo
        FROM public.produtos
        WHERE ativo = TRUE
        ORDER BY descricao
        """
    )

    condicoes = fetch_all(
        """
        SELECT
            id, nome, forma_pagamento, parcelas,
            dias_intervalo, taxa_percentual
        FROM public.condicoes_pagamento
        WHERE ativo = TRUE
        ORDER BY nome
        """
    )

    if request.method == "POST":
        cliente_id = request.form.get("cliente_id") or None
        observacoes = request.form.get("observacoes", "").strip()
        desconto_tipo = (request.form.get("desconto_tipo", "valor") or "valor").strip().lower()
        desconto_informado = parse_decimal(request.form.get("desconto", "0") or "0")

        produto_ids = request.form.getlist("produto_id[]")
        quantidades = request.form.getlist("quantidade[]")
        valores = request.form.getlist("valor_unitario[]")
        descontos_item_tipos = request.form.getlist("desconto_item_tipo[]")
        descontos_item_valores = request.form.getlist("desconto_item_valor[]")

        pagamento_cond_ids = request.form.getlist("pagamento_condicao_id[]")
        pagamento_valores = request.form.getlist("pagamento_valor[]")

        itens = []
        pagamentos = []
        valor_bruto = 0.0
        desconto_itens_total = 0.0

        empresa_config = fetch_one(
            """
            SELECT COALESCE(permite_estoque_negativo, FALSE) AS permite_estoque_negativo
            FROM public.empresas
            WHERE ativo = TRUE
            ORDER BY id
            LIMIT 1
            """
        )
        permite_estoque_negativo = bool(empresa_config["permite_estoque_negativo"]) if empresa_config else False

        try:
            for i in range(len(produto_ids)):
                if not produto_ids[i]:
                    continue

                produto_id = int(produto_ids[i])
                produto_db = fetch_one(
                    """
                    SELECT id, descricao, unidade, COALESCE(estoque_atual, 0) AS estoque_atual
                    FROM public.produtos
                    WHERE id = %s
                    """,
                    (produto_id,),
                )

                if not produto_db:
                    flash(f"Produto {produto_id} não encontrado.", "danger")
                    return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

                quantidade = parse_quantity_by_unidade(produto_db["unidade"], quantidades[i] if i < len(quantidades) else "0")
                valor_unitario = parse_decimal(valores[i] if i < len(valores) else "0")

                if quantidade <= 0:
                    continue

                if (not permite_estoque_negativo) and float(produto_db["estoque_atual"] or 0) < float(quantidade):
                    flash(f"Estoque insuficiente para {produto_db['descricao']}.", "danger")
                    return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

                total_item_bruto = float(quantidade) * float(valor_unitario)
                desconto_item_tipo = (descontos_item_tipos[i] if i < len(descontos_item_tipos) else "valor") or "valor"
                desconto_item_tipo = desconto_item_tipo.strip().lower()
                desconto_item_valor = parse_decimal(descontos_item_valores[i] if i < len(descontos_item_valores) else "0")
                if desconto_item_tipo == "percentual":
                    desconto_item_total = min((total_item_bruto * float(desconto_item_valor)) / 100.0, total_item_bruto)
                else:
                    desconto_item_total = min(float(desconto_item_valor), total_item_bruto)
                total_item_liquido = max(total_item_bruto - desconto_item_total, 0.0)

                observacao_estoque = None
                saldo_final = float(produto_db["estoque_atual"] or 0) - float(quantidade)
                if saldo_final < 0 and permite_estoque_negativo:
                    observacao_estoque = f"Ficará com estoque negativo: {saldo_final:g}"

                valor_bruto += total_item_bruto
                desconto_itens_total += desconto_item_total
                itens.append(
                    {
                        "produto_id": produto_id,
                        "quantidade": quantidade,
                        "valor_unitario": valor_unitario,
                        "desconto_tipo": desconto_item_tipo,
                        "desconto_valor": float(desconto_item_valor),
                        "desconto_total": desconto_item_total,
                        "total_bruto": total_item_bruto,
                        "total": total_item_liquido,
                        "observacao_estoque": observacao_estoque,
                    }
                )

            if not itens:
                flash("Adicione pelo menos um item à venda.", "warning")
                return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

            subtotal_liquido = max(float(valor_bruto) - float(desconto_itens_total), 0.0)
            if desconto_tipo == "percentual":
                desconto = min((subtotal_liquido * float(desconto_informado)) / 100.0, subtotal_liquido)
            else:
                desconto = min(float(desconto_informado), subtotal_liquido)
            valor_liquido = max(subtotal_liquido - float(desconto), 0.0)

            total_pagamentos = 0.0
            for i in range(max(len(pagamento_cond_ids), len(pagamento_valores))):
                cond_id_raw = pagamento_cond_ids[i] if i < len(pagamento_cond_ids) else ""
                valor_raw = pagamento_valores[i] if i < len(pagamento_valores) else ""

                if not cond_id_raw and not valor_raw:
                    continue
                if not cond_id_raw:
                    flash("Existe um lançamento de pagamento sem condição de pagamento.", "warning")
                    return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

                valor_pag = parse_decimal(valor_raw or "0")
                if valor_pag <= 0:
                    flash("Existe um lançamento de pagamento com valor inválido.", "warning")
                    return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

                cond = fetch_one(
                    """
                    SELECT id, nome, forma_pagamento, parcelas, dias_intervalo, taxa_percentual
                    FROM public.condicoes_pagamento
                    WHERE id = %s AND ativo = TRUE
                    """,
                    (int(cond_id_raw),),
                )
                if not cond:
                    flash("Uma das condições de pagamento informadas é inválida.", "danger")
                    return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

                pagamentos.append(
                    {
                        "condicao_id": cond["id"],
                        "nome": cond["nome"],
                        "forma_pagamento": cond["forma_pagamento"] or "",
                        "parcelas": int(cond["parcelas"] or 1),
                        "dias_intervalo": int(cond["dias_intervalo"] or 30),
                        "valor": float(valor_pag),
                    }
                )
                total_pagamentos += float(valor_pag)

            if not pagamentos:
                flash("Adicione pelo menos um lançamento de pagamento.", "warning")
                return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

            if round(total_pagamentos, 2) != round(valor_liquido, 2):
                flash(f"Os pagamentos lançados somam R$ {total_pagamentos:.2f} e a venda totaliza R$ {valor_liquido:.2f}. Ajuste antes de finalizar.", "danger")
                return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

            condicao_principal = pagamentos[0]
            condicoes_ids_texto = ",".join(str(p["condicao_id"]) for p in pagamentos)
            total_parcelas = sum(int(p["parcelas"]) for p in pagamentos) or 1
            valor_parcela_base = float(valor_liquido) / total_parcelas if total_parcelas > 0 else float(valor_liquido)

            conn = get_conn()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        INSERT INTO public.vendas (
                            cliente_id,
                            usuario_id,
                            valor_bruto,
                            desconto,
                            desconto_tipo,
                            desconto_itens_total,
                            desconto_geral_valor,
                            acrescimo,
                            valor_liquido,
                            forma_pagamento,
                            parcelas,
                            valor_parcela,
                            condicao_pagamento_id,
                            observacoes,
                            condicoes_pagamento_ids
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            cliente_id if cliente_id else None,
                            session["user_id"],
                            valor_bruto,
                            desconto,
                            desconto_tipo,
                            desconto_itens_total,
                            desconto,
                            0,
                            valor_liquido,
                            condicao_principal["forma_pagamento"],
                            total_parcelas,
                            valor_parcela_base,
                            condicao_principal["condicao_id"],
                            observacoes,
                            condicoes_ids_texto,
                        ),
                    )
                    venda_id = cur.fetchone()["id"]

                    for item in itens:
                        cur.execute(
                            """
                            INSERT INTO public.venda_itens (
                                venda_id,
                                produto_id,
                                quantidade,
                                valor_unitario,
                                desconto_tipo,
                                desconto_valor,
                                desconto_total,
                                total_bruto,
                                total_liquido,
                                total,
                                observacao_estoque
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                venda_id,
                                item["produto_id"],
                                item["quantidade"],
                                item["valor_unitario"],
                                item["desconto_tipo"],
                                item["desconto_valor"],
                                item["desconto_total"],
                                item["total_bruto"],
                                item["total"],
                                item["total"],
                                item["observacao_estoque"],
                            ),
                        )

                        cur.execute(
                            """
                            UPDATE public.produtos
                               SET estoque_atual = COALESCE(estoque_atual, 0) - %s
                             WHERE id = %s
                            """,
                            (item["quantidade"], item["produto_id"]),
                        )

                    data_base = date.today()

                    for pagamento in pagamentos:
                        cur.execute(
                            """
                            INSERT INTO public.vendas_pagamentos (
                                venda_id,
                                condicao_pagamento_id,
                                descricao_condicao,
                                forma_pagamento,
                                valor,
                                parcelas,
                                dias_intervalo
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            RETURNING id
                            """,
                            (
                                venda_id,
                                pagamento["condicao_id"],
                                pagamento["nome"],
                                pagamento["forma_pagamento"],
                                pagamento["valor"],
                                pagamento["parcelas"],
                                pagamento["dias_intervalo"],
                            ),
                        )
                        pagamento_id = cur.fetchone()["id"]

                        valor_parcela = float(pagamento["valor"]) / int(pagamento["parcelas"] or 1)
                        tem_pagamento_id = column_exists("vendas_vencimentos", "pagamento_id")

                        for numero in range(1, int(pagamento["parcelas"] or 1) + 1):
                            data_vencimento = data_base if int(pagamento["parcelas"] or 1) == 1 else data_base + timedelta(days=(numero - 1) * int(pagamento["dias_intervalo"] or 30))

                            if tem_pagamento_id:
                                cur.execute(
                                    """
                                    INSERT INTO public.vendas_vencimentos (
                                        venda_id,
                                        pagamento_id,
                                        numero_parcela,
                                        valor,
                                        data_vencimento,
                                        status
                                    )
                                    VALUES (%s, %s, %s, %s, %s, %s)
                                    """,
                                    (
                                        venda_id,
                                        pagamento_id,
                                        numero,
                                        round(valor_parcela, 2),
                                        data_vencimento,
                                        "PENDENTE",
                                    ),
                                )
                            else:
                                cur.execute(
                                    """
                                    INSERT INTO public.vendas_vencimentos (
                                        venda_id,
                                        numero_parcela,
                                        valor,
                                        data_vencimento,
                                        status
                                    )
                                    VALUES (%s, %s, %s, %s, %s)
                                    """,
                                    (
                                        venda_id,
                                        numero,
                                        round(valor_parcela, 2),
                                        data_vencimento,
                                        "PENDENTE",
                                    ),
                                )

                conn.commit()
                flash("Venda cadastrada com sucesso.", "success")
                return redirect(url_for("vendas"))
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        except ValueError:
            flash("Valores inválidos na venda.", "danger")

    return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

@app.route("/empresas")
@login_required
def empresas():
    rows = fetch_all(
        """
        SELECT
            id, nome_fantasia, razao_social, cnpj, telefone, email,
            cidade, estado, responsavel, COALESCE(permite_estoque_negativo, FALSE) AS permite_estoque_negativo,
            ativo, created_at
        FROM public.empresas
        ORDER BY nome_fantasia
        """
    )
    return render_template("empresas.html", empresas=rows)


@app.route("/empresas/nova", methods=["GET", "POST"])
@login_required
def empresas_nova():
    if request.method == "POST":
        nome_fantasia = request.form.get("nome_fantasia", "").strip()
        razao_social = request.form.get("razao_social", "").strip()
        cnpj = request.form.get("cnpj", "").strip()
        telefone = request.form.get("telefone", "").strip()
        email = request.form.get("email", "").strip()
        cidade = request.form.get("cidade", "").strip()
        estado = request.form.get("estado", "").strip().upper()
        responsavel = request.form.get("responsavel", "").strip()
        permite_estoque_negativo = "permite_estoque_negativo" in request.form
        ativo = "ativo" in request.form

        if not nome_fantasia:
            flash("Informe o nome fantasia da empresa.", "warning")
            return render_template("empresa_form.html", empresa=None)

        execute(
            """
            INSERT INTO public.empresas (
                nome_fantasia, razao_social, cnpj, telefone, email,
                cidade, estado, responsavel, permite_estoque_negativo, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                nome_fantasia, razao_social, cnpj, telefone, email,
                cidade, estado, responsavel, permite_estoque_negativo, ativo
            ),
        )
        flash("Empresa cadastrada com sucesso.", "success")
        return redirect(url_for("empresas"))

    return render_template("empresa_form.html", empresa=None)


@app.route("/empresas/<int:empresa_id>/editar", methods=["GET", "POST"])
@login_required
def empresas_editar(empresa_id):
    empresa = fetch_one(
        """
        SELECT
            id, nome_fantasia, razao_social, cnpj, telefone, email,
            cidade, estado, responsavel, COALESCE(permite_estoque_negativo, FALSE) AS permite_estoque_negativo,
            ativo, created_at
        FROM public.empresas
        WHERE id = %s
        """,
        (empresa_id,),
    )

    if not empresa:
        flash("Empresa não encontrada.", "warning")
        return redirect(url_for("empresas"))

    if request.method == "POST":
        nome_fantasia = request.form.get("nome_fantasia", "").strip()
        razao_social = request.form.get("razao_social", "").strip()
        cnpj = request.form.get("cnpj", "").strip()
        telefone = request.form.get("telefone", "").strip()
        email = request.form.get("email", "").strip()
        cidade = request.form.get("cidade", "").strip()
        estado = request.form.get("estado", "").strip().upper()
        responsavel = request.form.get("responsavel", "").strip()
        permite_estoque_negativo = "permite_estoque_negativo" in request.form
        ativo = "ativo" in request.form

        if not nome_fantasia:
            flash("Informe o nome fantasia da empresa.", "warning")
            empresa.update({
                "nome_fantasia": nome_fantasia,
                "razao_social": razao_social,
                "cnpj": cnpj,
                "telefone": telefone,
                "email": email,
                "cidade": cidade,
                "estado": estado,
                "responsavel": responsavel,
                "permite_estoque_negativo": permite_estoque_negativo,
                "ativo": ativo,
            })
            return render_template("empresa_form.html", empresa=empresa)

        execute(
            """
            UPDATE public.empresas
               SET nome_fantasia = %s,
                   razao_social = %s,
                   cnpj = %s,
                   telefone = %s,
                   email = %s,
                   cidade = %s,
                   estado = %s,
                   responsavel = %s,
                   permite_estoque_negativo = %s,
                   ativo = %s
             WHERE id = %s
            """,
            (
                nome_fantasia, razao_social, cnpj, telefone, email,
                cidade, estado, responsavel, permite_estoque_negativo, ativo, empresa_id
            ),
        )
        flash("Empresa atualizada com sucesso.", "success")
        return redirect(url_for("empresas"))

    return render_template("empresa_form.html", empresa=empresa)





@app.route("/api/produto/<int:produto_id>/estoque")
@login_required
def api_produto_estoque(produto_id):
    produto = fetch_one(
        """
        SELECT
            id,
            descricao,
            unidade,
            COALESCE(estoque_atual, 0) AS estoque,
            COALESCE(preco_venda, 0) AS preco
        FROM public.produtos
        WHERE id = %s
        """,
        (produto_id,),
    )

    empresa = fetch_one(
        """
        SELECT COALESCE(permite_estoque_negativo, FALSE) AS permite_estoque_negativo
        FROM public.empresas
        WHERE ativo = TRUE
        ORDER BY id
        LIMIT 1
        """
    )

    if not produto:
        return {"ok": False, "message": "Produto não encontrado."}, 404

    permite = bool(empresa["permite_estoque_negativo"]) if empresa else False
    return {
        "ok": True,
        "produto_id": produto["id"],
        "descricao": produto["descricao"],
        "estoque": float(produto["estoque"] or 0),
        "preco": float(produto["preco"] or 0),
        "unidade": produto["unidade"],
        "permite_estoque_negativo": permite,
    }

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    ensure_schema()
    app.run(host="0.0.0.0", port=5000, debug=True)
