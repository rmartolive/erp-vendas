import os
import hashlib
import secrets
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
    send_from_directory,
)
import psycopg2
from psycopg2.extras import RealDictCursor


# =========================================================
# ENV
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
LOGOS_DIR = UPLOADS_DIR / "logos"


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


# Tenta carregar .env primeiro da pasta do projeto e depois do diretÃ³rio atual.
# Assim funciona tanto ao rodar pelo terminal dentro da pasta quanto por atalho/IDE.
load_env_file(BASE_DIR / ".env")
load_env_file(Path.cwd() / ".env")


# =========================================================
# APP
# =========================================================
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.secret_key = os.getenv("FLASK_SECRET_KEY", "troque_essa_chave_em_producao")
schema_checked = False


@app.template_filter("format_qty")
def format_qty(value):
    try:
        numero = float(value or 0)
    except Exception:
        return "0"

    texto = f"{numero:.3f}".rstrip("0").rstrip(".")
    return texto.replace(".", ",") if texto else "0"


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
        text = str(value).strip()
        if text == "":
            return float(default)
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
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


def obter_familia_por_id(familia_id):
    return fetch_one(
        """
        SELECT id, nome, ativo, created_at
        FROM public.familias_produtos
        WHERE id = %s
        """,
        (familia_id,),
    )


def get_empresa_configuracoes():
    return fetch_one(
        """
        SELECT *
        FROM public.empresas
        WHERE ativo = TRUE
        ORDER BY id
        LIMIT 1
        """
    ) or {}


def get_empresa_branding():
    empresa = get_empresa_configuracoes()
    nome_base = (empresa.get("nome_fantasia") or empresa.get("razao_social") or "").strip()
    brand_name = f"{nome_base} ERP" if nome_base else "ERP Vendas"
    logo_path = (empresa.get("logo_path") or "").strip()
    logo_url = url_for("empresa_logo", filename=logo_path) if logo_path else None
    return {
        "empresa_nome_base": nome_base,
        "empresa_brand_name": brand_name,
        "empresa_logo_url": logo_url,
        "empresa_support_url": "https://wa.me/5521980912630",
    }


def logo_jpeg_valido(file_storage):
    if not file_storage or not getattr(file_storage, "filename", ""):
        return False
    filename = file_storage.filename.lower()
    mimetype = (file_storage.mimetype or "").lower()
    return filename.endswith((".jpg", ".jpeg")) and mimetype in ("image/jpeg", "image/pjpeg")


def salvar_logo_empresa(file_storage, empresa_id):
    if not file_storage or not getattr(file_storage, "filename", ""):
        return None
    if not logo_jpeg_valido(file_storage):
        raise ValueError("O logo deve ser um arquivo JPEG (.jpg ou .jpeg).")

    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    nome_arquivo = f"empresa_{empresa_id}.jpg"
    destino = LOGOS_DIR / nome_arquivo
    file_storage.save(destino)
    return nome_arquivo


def registrar_movimentacao_estoque(
    produto_id,
    tipo_movimento,
    quantidade,
    estoque_anterior=None,
    estoque_posterior=None,
    origem=None,
    referencia_id=None,
    observacao=None,
    usuario_id=None,
    cur=None,
):
    if not quantidade:
        return

    quantidade = float(quantidade or 0)
    if abs(quantidade) <= 0.000001:
        return

    usuario_id = usuario_id if usuario_id is not None else session.get("user_id")
    sql = """
        INSERT INTO public.movimentacoes_estoque (
            produto_id,
            usuario_id,
            tipo_movimento,
            origem,
            referencia_id,
            quantidade,
            estoque_anterior,
            estoque_posterior,
            observacao
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    params = (
        produto_id,
        usuario_id,
        tipo_movimento,
        origem,
        referencia_id,
        quantidade,
        estoque_anterior,
        estoque_posterior,
        observacao,
    )

    if cur is not None:
        cur.execute(sql, params)
        return

    execute(sql, params)


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


def ensure_column(table_name, column_name, definition, schema="public"):
    if column_exists(table_name, column_name, schema=schema):
        return

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"ALTER TABLE {schema}.{table_name} ADD COLUMN IF NOT EXISTS {column_name} {definition}"
            )
        conn.commit()
    except psycopg2.errors.InsufficientPrivilege:
        conn.rollback()
        print(
            f"[ensure_schema] Sem permissÃ£o para criar a coluna {schema}.{table_name}.{column_name}."
        )
    finally:
        conn.close()

def ensure_schema():
    ddl_statements = [
        """
        CREATE TABLE IF NOT EXISTS public.usuarios (
            id BIGSERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            perfil VARCHAR(20) NOT NULL CHECK (perfil IN ('admin', 'atendente')),
            pode_vender_abaixo_custo BOOLEAN DEFAULT FALSE,
            desconto_maximo_percentual NUMERIC(5,2) DEFAULT 0,
            permite_login_multiplo BOOLEAN DEFAULT TRUE,
            permite_editar_venda BOOLEAN DEFAULT FALSE,
            permite_estornar_venda BOOLEAN DEFAULT FALSE,
            permite_excluir_venda BOOLEAN DEFAULT FALSE,
            permite_inventario BOOLEAN DEFAULT FALSE,
            session_token TEXT,
            ativo BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE public.usuarios
            ADD COLUMN IF NOT EXISTS pode_vender_abaixo_custo BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS desconto_maximo_percentual NUMERIC(5,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS permite_login_multiplo BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS permite_editar_venda BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_estornar_venda BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_excluir_venda BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_inventario BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS session_token TEXT
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
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'familias_produto'
            ) AND NOT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'familias_produtos'
            ) THEN
                ALTER TABLE public.familias_produto RENAME TO familias_produtos;
            END IF;
        END $$;
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
        CREATE TABLE IF NOT EXISTS public.movimentacoes_estoque (
            id BIGSERIAL PRIMARY KEY,
            produto_id BIGINT NOT NULL REFERENCES public.produtos(id) ON DELETE CASCADE,
            usuario_id BIGINT REFERENCES public.usuarios(id),
            tipo_movimento VARCHAR(40) NOT NULL,
            origem VARCHAR(40),
            referencia_id BIGINT,
            quantidade NUMERIC(14,3) NOT NULL DEFAULT 0,
            estoque_anterior NUMERIC(14,3),
            estoque_posterior NUMERIC(14,3),
            observacao TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
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
            produto_codigo_obrigatorio BOOLEAN DEFAULT FALSE,
            produto_familia_obrigatoria BOOLEAN DEFAULT FALSE,
            produto_unidade_obrigatoria BOOLEAN DEFAULT FALSE,
            produto_custo_obrigatorio BOOLEAN DEFAULT FALSE,
            produto_preco_venda_obrigatorio BOOLEAN DEFAULT FALSE,
            produto_estoque_atual_obrigatorio BOOLEAN DEFAULT FALSE,
            produto_estoque_minimo_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_tipo_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_nome_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_documento_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_telefone_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_email_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_cep_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_endereco_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_cidade_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_estado_obrigatorio BOOLEAN DEFAULT FALSE,
            cliente_observacoes_obrigatorio BOOLEAN DEFAULT FALSE,
            logo_path TEXT,
            permite_estoque_negativo BOOLEAN DEFAULT FALSE,
            permite_desconto BOOLEAN DEFAULT TRUE,
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
            ADD COLUMN IF NOT EXISTS produto_codigo_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS produto_familia_obrigatoria BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS produto_unidade_obrigatoria BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS produto_custo_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS produto_preco_venda_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS produto_estoque_atual_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS produto_estoque_minimo_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_tipo_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_nome_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_documento_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_telefone_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_email_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_cep_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_endereco_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_cidade_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_estado_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS cliente_observacoes_obrigatorio BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS logo_path TEXT,
            ADD COLUMN IF NOT EXISTS permite_estoque_negativo BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_desconto BOOLEAN DEFAULT TRUE,
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
            IF EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name = 'produtos'
                  AND constraint_name = 'fk_produtos_familia'
            ) AND EXISTS (
                SELECT 1
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace nt ON nt.oid = t.relnamespace
                JOIN pg_class ref ON ref.oid = c.confrelid
                JOIN pg_namespace nr ON nr.oid = ref.relnamespace
                WHERE c.conname = 'fk_produtos_familia'
                  AND nt.nspname = 'public'
                  AND t.relname = 'produtos'
                  AND (nr.nspname <> 'public' OR ref.relname <> 'familias_produtos')
            ) THEN
                ALTER TABLE public.produtos DROP CONSTRAINT fk_produtos_familia;
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
                    print("[ensure_schema] Sem permissÃ£o para alterar uma tabela existente. Vou seguir sem aplicar essa migraÃ§Ã£o automÃ¡tica.")
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

    # Alguns bancos antigos falham silenciosamente em ALTER TABLE com vÃ¡rias colunas.
    # Aqui garantimos as colunas crÃ­ticas de descontos e totais individualmente.
    ensure_column("venda_itens", "desconto_tipo", "VARCHAR(20) DEFAULT 'valor'")
    ensure_column("venda_itens", "desconto_valor", "NUMERIC(14,2) DEFAULT 0")
    ensure_column("venda_itens", "desconto_total", "NUMERIC(14,2) DEFAULT 0")
    ensure_column("venda_itens", "total_bruto", "NUMERIC(14,2) DEFAULT 0")
    ensure_column("venda_itens", "total_liquido", "NUMERIC(14,2) DEFAULT 0")
    ensure_column("venda_itens", "observacao_estoque", "TEXT")
    ensure_column("vendas", "desconto_tipo", "VARCHAR(20) DEFAULT 'valor'")
    ensure_column("vendas", "desconto_itens_total", "NUMERIC(14,2) DEFAULT 0")
    ensure_column("vendas", "desconto_geral_valor", "NUMERIC(14,2) DEFAULT 0")

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
    branding = get_empresa_branding()
    return {
        "session_user_nome": session.get("nome"),
        "session_user_perfil": session.get("perfil"),
        **branding,
    }


@app.before_request
def ensure_schema_before_requests():
    global schema_checked
    if schema_checked:
        return
    ensure_schema()
    schema_checked = True


@app.before_request
def validate_user_session():
    user_id = session.get("user_id")
    session_token = session.get("session_token")
    if not user_id or not session_token:
        return

    user = fetch_one(
        """
        SELECT COALESCE(permite_login_multiplo, TRUE) AS permite_login_multiplo,
               session_token,
               ativo
        FROM public.usuarios
        WHERE id = %s
        """,
        (user_id,),
    )
    if not user or not user["ativo"]:
        session.clear()
        return redirect(url_for("login"))

    if not user["permite_login_multiplo"] and user.get("session_token") != session_token:
        session.clear()
        flash("Seu acesso foi encerrado porque este usuário entrou em outra sessão.", "warning")
        return redirect(url_for("login"))


@app.route("/media/logos/<path:filename>")
def empresa_logo(filename):
    return send_from_directory(LOGOS_DIR, filename)


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
            SELECT id, nome, username, perfil, ativo, password_hash,
                   COALESCE(permite_login_multiplo, TRUE) AS permite_login_multiplo
            FROM public.usuarios
            WHERE username = %s
            """,
            (username,),
        )

        if not user:
            flash("UsuÃ¡rio nÃ£o encontrado.", "danger")
            return render_template("login.html")

        if not user["ativo"]:
            flash("UsuÃ¡rio inativo.", "danger")
            return render_template("login.html")

        if user["password_hash"] != hash_password(password):
            flash("Senha invÃ¡lida.", "danger")
            return render_template("login.html")

        session_token = secrets.token_hex(16)
        if not user["permite_login_multiplo"]:
            execute(
                """
                UPDATE public.usuarios
                   SET session_token = %s
                 WHERE id = %s
                """,
                (session_token, user["id"]),
            )

        session["user_id"] = user["id"]
        session["nome"] = user["nome"]
        session["username"] = user["username"]
        session["perfil"] = user["perfil"]
        session["session_token"] = session_token if not user["permite_login_multiplo"] else secrets.token_hex(16)

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
    cliente_id_raw = (request.args.get("cliente_id") or "").strip()
    data_ini = (request.args.get("data_ini") or "").strip()
    data_fim = (request.args.get("data_fim") or "").strip()

    cliente_id = None
    filtros = []
    params = []

    if cliente_id_raw:
        try:
            cliente_id = int(cliente_id_raw)
        except ValueError:
            cliente_id = None

    if cliente_id:
        filtros.append("v.cliente_id = %s")
        params.append(cliente_id)
    if data_ini:
        filtros.append("DATE(v.data_venda) >= %s")
        params.append(data_ini)
    if data_fim:
        filtros.append("DATE(v.data_venda) <= %s")
        params.append(data_fim)

    if column_exists("vendas", "status"):
        filtros.append("COALESCE(v.status, 'ATIVA') = 'ATIVA'")

    where_clause = (" WHERE " + " AND ".join(filtros)) if filtros else ""
    base_params = tuple(params)

    clientes = fetch_all(
        """
        SELECT id, nome
        FROM public.pessoas
        WHERE ativo = TRUE
        ORDER BY nome
        """
    )

    kpis = fetch_one(
        f"""
        SELECT
            COUNT(DISTINCT v.id) AS quantidade_vendas,
            COALESCE(AVG(v.valor_liquido), 0) AS ticket_medio
        FROM public.vendas v
        {where_clause}
        """,
        base_params,
    ) or {"quantidade_vendas": 0, "ticket_medio": 0}

    vendas_por_categoria = fetch_all(
        f"""
        SELECT
            COALESCE(f.nome, p.categoria, 'Sem familia') AS categoria,
            COALESCE(SUM(COALESCE(vi.total, 0)), 0) AS valor_venda,
            COALESCE(SUM(COALESCE(vi.quantidade, 0) * COALESCE(p.custo, 0)), 0) AS custo,
            COALESCE(SUM(COALESCE(vi.total, 0)) - SUM(COALESCE(vi.quantidade, 0) * COALESCE(p.custo, 0)), 0) AS liquido
        FROM public.venda_itens vi
        INNER JOIN public.vendas v ON v.id = vi.venda_id
        LEFT JOIN public.produtos p ON p.id = vi.produto_id
        LEFT JOIN public.familias_produtos f ON f.id = p.familia_id
        {where_clause}
        GROUP BY COALESCE(f.nome, p.categoria, 'Sem familia')
        ORDER BY valor_venda DESC, categoria
        """,
        base_params,
    )
    for row in vendas_por_categoria:
        valor_venda = float(row["valor_venda"] or 0)
        liquido = float(row["liquido"] or 0)
        row["margem_percentual"] = (liquido / valor_venda * 100.0) if valor_venda > 0 else 0.0

    vendas_listadas = fetch_all(
        f"""
        SELECT
            v.id AS venda_id,
            v.data_venda,
            p.nome AS cliente,
            pr.descricao AS produto,
            COALESCE(f.nome, pr.categoria, 'Sem familia') AS categoria,
            vi.quantidade,
            vi.valor_unitario,
            COALESCE(vi.total, 0) AS total_item
        FROM public.venda_itens vi
        INNER JOIN public.vendas v ON v.id = vi.venda_id
        LEFT JOIN public.pessoas p ON p.id = v.cliente_id
        LEFT JOIN public.produtos pr ON pr.id = vi.produto_id
        LEFT JOIN public.familias_produtos f ON f.id = pr.familia_id
        {where_clause}
        ORDER BY v.data_venda DESC, v.id DESC, vi.id DESC
        LIMIT 80
        """,
        base_params,
    )

    ranking_produtos = fetch_all(
        f"""
        SELECT
            pr.id,
            pr.descricao AS produto,
            COALESCE(f.nome, pr.categoria, 'Sem familia') AS categoria,
            COALESCE(SUM(vi.quantidade), 0) AS quantidade_vendida,
            COALESCE(SUM(vi.total), 0) AS valor_vendido
        FROM public.venda_itens vi
        INNER JOIN public.vendas v ON v.id = vi.venda_id
        LEFT JOIN public.produtos pr ON pr.id = vi.produto_id
        LEFT JOIN public.familias_produtos f ON f.id = pr.familia_id
        {where_clause}
        GROUP BY pr.id, pr.descricao, COALESCE(f.nome, pr.categoria, 'Sem familia')
        ORDER BY quantidade_vendida DESC, valor_vendido DESC, pr.descricao
        LIMIT 15
        """,
        base_params,
    )

    cliente_selecionado = None
    if cliente_id:
        cliente_selecionado = next((c for c in clientes if c["id"] == cliente_id), None)

    return render_template(
        "dashboard.html",
        clientes=clientes,
        cliente_id=cliente_id,
        cliente_nome=cliente_selecionado["nome"] if cliente_selecionado else "",
        data_ini=data_ini,
        data_fim=data_fim,
        quantidade_vendas=int(kpis["quantidade_vendas"] or 0),
        ticket_medio=float(kpis["ticket_medio"] or 0),
        vendas_por_categoria=vendas_por_categoria,
        vendas_listadas=vendas_listadas,
        ranking_produtos=ranking_produtos,
    )


# =========================================================
# USUÃRIOS
# =========================================================
@app.route("/usuarios")
@admin_required
def usuarios():
    rows = fetch_all(
        """
        SELECT id, nome, username, perfil,
               COALESCE(pode_vender_abaixo_custo, FALSE) AS pode_vender_abaixo_custo,
               COALESCE(desconto_maximo_percentual, 0) AS desconto_maximo_percentual,
               COALESCE(permite_login_multiplo, TRUE) AS permite_login_multiplo,
               COALESCE(permite_editar_venda, FALSE) AS permite_editar_venda,
               COALESCE(permite_estornar_venda, FALSE) AS permite_estornar_venda,
               COALESCE(permite_excluir_venda, FALSE) AS permite_excluir_venda,
               COALESCE(permite_inventario, FALSE) AS permite_inventario,
               ativo, created_at
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
    pode_vender_abaixo_custo = "pode_vender_abaixo_custo" in request.form
    desconto_maximo_percentual = request.form.get("desconto_maximo_percentual", "0").strip() or "0"
    permite_login_multiplo = "permite_login_multiplo" in request.form
    permite_editar_venda = "permite_editar_venda" in request.form
    permite_estornar_venda = "permite_estornar_venda" in request.form
    permite_excluir_venda = "permite_excluir_venda" in request.form
    permite_inventario = "permite_inventario" in request.form
    ativo = "ativo" in request.form

    if not nome or not username or not senha:
        flash("Preencha nome, usuÃ¡rio e senha.", "warning")
        return redirect(url_for("usuarios"))

    try:
        execute(
            """
            INSERT INTO public.usuarios
            (
                nome, username, password_hash, perfil,
                pode_vender_abaixo_custo, desconto_maximo_percentual,
                permite_login_multiplo, permite_editar_venda, permite_estornar_venda,
                permite_excluir_venda, permite_inventario,
                ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                nome, username, hash_password(senha), perfil,
                pode_vender_abaixo_custo, desconto_maximo_percentual,
                permite_login_multiplo, permite_editar_venda, permite_estornar_venda,
                permite_excluir_venda, permite_inventario,
                ativo
            ),
        )
        flash("UsuÃ¡rio cadastrado com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar usuÃ¡rio: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("usuarios"))


@app.route("/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@admin_required
def usuarios_editar(usuario_id):
    usuario = fetch_one(
        """
        SELECT id, nome, username, perfil,
               COALESCE(pode_vender_abaixo_custo, FALSE) AS pode_vender_abaixo_custo,
               COALESCE(desconto_maximo_percentual, 0) AS desconto_maximo_percentual,
               COALESCE(permite_login_multiplo, TRUE) AS permite_login_multiplo,
               COALESCE(permite_editar_venda, FALSE) AS permite_editar_venda,
               COALESCE(permite_estornar_venda, FALSE) AS permite_estornar_venda,
               COALESCE(permite_excluir_venda, FALSE) AS permite_excluir_venda,
               COALESCE(permite_inventario, FALSE) AS permite_inventario,
               ativo
        FROM public.usuarios
        WHERE id = %s
        """,
        (usuario_id,),
    )

    if not usuario:
        flash("Usuário não encontrado.", "warning")
        return redirect(url_for("usuarios"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        username = request.form.get("username", "").strip()
        senha = request.form.get("senha", "")
        perfil = request.form.get("perfil", "atendente")
        pode_vender_abaixo_custo = "pode_vender_abaixo_custo" in request.form
        desconto_maximo_percentual = request.form.get("desconto_maximo_percentual", "0").strip() or "0"
        permite_login_multiplo = "permite_login_multiplo" in request.form
        permite_editar_venda = "permite_editar_venda" in request.form
        permite_estornar_venda = "permite_estornar_venda" in request.form
        permite_excluir_venda = "permite_excluir_venda" in request.form
        permite_inventario = "permite_inventario" in request.form
        ativo = "ativo" in request.form

        if not nome or not username:
            flash("Preencha nome e usuário.", "warning")
            return render_template("usuario_editar.html", usuario=usuario)

        try:
            if senha:
                execute(
                    """
                    UPDATE public.usuarios
                       SET nome = %s,
                           username = %s,
                           password_hash = %s,
                           perfil = %s,
                           pode_vender_abaixo_custo = %s,
                           desconto_maximo_percentual = %s,
                           permite_login_multiplo = %s,
                           permite_editar_venda = %s,
                           permite_estornar_venda = %s,
                           permite_excluir_venda = %s,
                           permite_inventario = %s,
                           ativo = %s
                     WHERE id = %s
                    """,
                    (
                        nome, username, hash_password(senha), perfil,
                        pode_vender_abaixo_custo, desconto_maximo_percentual,
                        permite_login_multiplo, permite_editar_venda, permite_estornar_venda,
                        permite_excluir_venda, permite_inventario,
                        ativo, usuario_id
                    ),
                )
            else:
                execute(
                    """
                    UPDATE public.usuarios
                       SET nome = %s,
                           username = %s,
                           perfil = %s,
                           pode_vender_abaixo_custo = %s,
                           desconto_maximo_percentual = %s,
                           permite_login_multiplo = %s,
                           permite_editar_venda = %s,
                           permite_estornar_venda = %s,
                           permite_excluir_venda = %s,
                           permite_inventario = %s,
                           ativo = %s
                     WHERE id = %s
                    """,
                    (
                        nome, username, perfil,
                        pode_vender_abaixo_custo, desconto_maximo_percentual,
                        permite_login_multiplo, permite_editar_venda, permite_estornar_venda,
                        permite_excluir_venda, permite_inventario,
                        ativo, usuario_id
                    ),
                )
            flash("Usuário atualizado com sucesso.", "success")
            return redirect(url_for("usuarios"))
        except psycopg2.Error as e:
            flash(f"Erro ao atualizar usuário: {e.pgerror or str(e)}", "danger")
            usuario.update(
                {
                    "nome": nome,
                    "username": username,
                    "perfil": perfil,
                    "pode_vender_abaixo_custo": pode_vender_abaixo_custo,
                    "desconto_maximo_percentual": desconto_maximo_percentual,
                    "permite_login_multiplo": permite_login_multiplo,
                    "permite_editar_venda": permite_editar_venda,
                    "permite_estornar_venda": permite_estornar_venda,
                    "permite_excluir_venda": permite_excluir_venda,
                    "permite_inventario": permite_inventario,
                    "ativo": ativo,
                }
            )

    return render_template("usuario_editar.html", usuario=usuario)


# =========================================================
# PESSOAS / CLIENTES
# =========================================================
@app.route("/pessoas")
@login_required
def pessoas():
    termo = request.args.get("q", "").strip()
    config = get_empresa_configuracoes()

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

    return render_template("pessoas.html", pessoas=rows, termo=termo, config=config)


@app.route("/pessoas/novo", methods=["POST"])
@login_required
def pessoas_novo():
    config = get_empresa_configuracoes()
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

    if bool(config.get("cliente_tipo_obrigatorio")) and not tipo:
        flash("O tipo Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("pessoas"))
    if (bool(config.get("cliente_nome_obrigatorio")) or not config) and not nome:
        flash("O nome Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("pessoas"))
    if bool(config.get("cliente_documento_obrigatorio")) and not documento:
        flash("CPF/CNPJ Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("pessoas"))
    if bool(config.get("cliente_telefone_obrigatorio")) and not telefone:
        flash("Telefone Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("pessoas"))
    if bool(config.get("cliente_email_obrigatorio")) and not email:
        flash("E-mail Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("pessoas"))
    if bool(config.get("cliente_cep_obrigatorio")) and not cep:
        flash("CEP Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("pessoas"))
    if bool(config.get("cliente_endereco_obrigatorio")) and not endereco:
        flash("EndereÃ§o Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("pessoas"))
    if bool(config.get("cliente_cidade_obrigatorio")) and not cidade:
        flash("Cidade Ã© obrigatÃ³ria.", "warning")
        return redirect(url_for("pessoas"))
    if bool(config.get("cliente_estado_obrigatorio")) and not estado:
        flash("UF Ã© obrigatÃ³ria.", "warning")
        return redirect(url_for("pessoas"))
    if bool(config.get("cliente_observacoes_obrigatorio")) and not observacoes:
        flash("ObservaÃ§Ãµes Ã© obrigatÃ³rio.", "warning")
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


@app.route("/pessoas/<int:pessoa_id>/editar", methods=["GET", "POST"])
@login_required
def pessoas_editar(pessoa_id):
    config = get_empresa_configuracoes()
    pessoa = fetch_one(
        """
        SELECT
            id, tipo, nome, documento, telefone, email,
            endereco, cidade, estado, cep, observacoes, ativo, created_at
        FROM public.pessoas
        WHERE id = %s
        """,
        (pessoa_id,),
    )

    if not pessoa:
        flash("Cliente não encontrado.", "warning")
        return redirect(url_for("pessoas"))

    if request.method == "POST":
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

        if bool(config.get("cliente_tipo_obrigatorio")) and not tipo:
            flash("O tipo é obrigatório.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)
        if (bool(config.get("cliente_nome_obrigatorio")) or not config) and not nome:
            flash("O nome é obrigatório.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)
        if bool(config.get("cliente_documento_obrigatorio")) and not documento:
            flash("CPF/CNPJ é obrigatório.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)
        if bool(config.get("cliente_telefone_obrigatorio")) and not telefone:
            flash("Telefone é obrigatório.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)
        if bool(config.get("cliente_email_obrigatorio")) and not email:
            flash("E-mail é obrigatório.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)
        if bool(config.get("cliente_cep_obrigatorio")) and not cep:
            flash("CEP é obrigatório.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)
        if bool(config.get("cliente_endereco_obrigatorio")) and not endereco:
            flash("Endereço é obrigatório.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)
        if bool(config.get("cliente_cidade_obrigatorio")) and not cidade:
            flash("Cidade é obrigatória.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)
        if bool(config.get("cliente_estado_obrigatorio")) and not estado:
            flash("UF é obrigatória.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)
        if bool(config.get("cliente_observacoes_obrigatorio")) and not observacoes:
            flash("Observações é obrigatório.", "warning")
            return render_template("pessoa_editar.html", pessoa=pessoa, config=config)

        try:
            execute(
                """
                UPDATE public.pessoas
                   SET tipo = %s,
                       nome = %s,
                       documento = %s,
                       telefone = %s,
                       email = %s,
                       endereco = %s,
                       cidade = %s,
                       estado = %s,
                       cep = %s,
                       observacoes = %s,
                       ativo = %s
                 WHERE id = %s
                """,
                (
                    tipo,
                    nome,
                    documento,
                    telefone,
                    email,
                    endereco,
                    cidade,
                    estado,
                    cep,
                    observacoes,
                    ativo,
                    pessoa_id,
                ),
            )
            flash("Cliente atualizado com sucesso.", "success")
            return redirect(url_for("pessoas"))
        except psycopg2.Error as e:
            flash(f"Erro ao atualizar cliente: {e.pgerror or str(e)}", "danger")
            pessoa.update(
                {
                    "tipo": tipo,
                    "nome": nome,
                    "documento": documento,
                    "telefone": telefone,
                    "email": email,
                    "endereco": endereco,
                    "cidade": cidade,
                    "estado": estado,
                    "cep": cep,
                    "observacoes": observacoes,
                    "ativo": ativo,
                }
            )

    return render_template("pessoa_editar.html", pessoa=pessoa, config=config)


# =========================================================
# PRODUTOS
# =========================================================
@app.route("/produtos")
@login_required
def produtos():
    termo = request.args.get("q", "").strip()
    familias = get_familias_produtos(apenas_ativas=False)
    config = get_empresa_configuracoes()

    if termo:
        rows = fetch_all(
            """
            SELECT
                p.id, p.codigo, p.descricao,
                COALESCE(f.nome, p.categoria) AS categoria,
                p.unidade, p.custo, p.preco_venda,
                p.estoque_atual, p.estoque_minimo,
                p.ativo, p.created_at
            FROM public.produtos p
            LEFT JOIN public.familias_produtos f ON f.id = p.familia_id
            WHERE p.descricao ILIKE %s
               OR COALESCE(p.codigo, '') ILIKE %s
               OR COALESCE(f.nome, p.categoria, '') ILIKE %s
            ORDER BY p.id DESC
            """,
            (f"%{termo}%", f"%{termo}%", f"%{termo}%"),
        )
    else:
        rows = fetch_all(
            """
            SELECT
                p.id, p.codigo, p.descricao,
                COALESCE(f.nome, p.categoria) AS categoria,
                p.unidade, p.custo, p.preco_venda,
                p.estoque_atual, p.estoque_minimo,
                p.ativo, p.created_at
            FROM public.produtos p
            LEFT JOIN public.familias_produtos f ON f.id = p.familia_id
            ORDER BY p.id DESC
            """
        )

    return render_template("produtos.html", produtos=rows, termo=termo, familias=familias, config=config)


@app.route("/relatorios/estoque-minimo")
@login_required
def relatorio_estoque_minimo():
    termo = request.args.get("q", "").strip()
    params = ()
    where = ""

    if termo:
        where = """
        WHERE p.descricao ILIKE %s
           OR COALESCE(p.codigo, '') ILIKE %s
           OR COALESCE(f.nome, p.categoria, '') ILIKE %s
        """
        params = (f"%{termo}%", f"%{termo}%", f"%{termo}%")

    produtos = fetch_all(
        f"""
        SELECT
            p.id,
            p.codigo,
            p.descricao,
            COALESCE(f.nome, p.categoria, 'Sem familia') AS categoria,
            p.unidade,
            COALESCE(p.estoque_atual, 0) AS estoque_atual,
            COALESCE(p.estoque_minimo, 0) AS estoque_minimo
        FROM public.produtos p
        LEFT JOIN public.familias_produtos f ON f.id = p.familia_id
        {where}
        ORDER BY
            (COALESCE(p.estoque_minimo, 0) - COALESCE(p.estoque_atual, 0)) DESC,
            p.descricao
        """,
        params,
    )

    total_produtos = len(produtos)
    abaixo_minimo = 0
    no_minimo = 0
    acima_minimo = 0
    total_sugerido = 0.0

    for produto in produtos:
        estoque_atual = float(produto["estoque_atual"] or 0)
        estoque_minimo = float(produto["estoque_minimo"] or 0)
        saldo_minimo = estoque_atual - estoque_minimo
        sugestao_compra = max(estoque_minimo - estoque_atual, 0.0)

        produto["saldo_minimo"] = saldo_minimo
        produto["sugestao_compra"] = sugestao_compra

        if saldo_minimo < 0:
            produto["status_estoque"] = "abaixo"
            abaixo_minimo += 1
            total_sugerido += sugestao_compra
        elif saldo_minimo == 0:
            produto["status_estoque"] = "ideal"
            no_minimo += 1
        else:
            produto["status_estoque"] = "acima"
            acima_minimo += 1

    return render_template(
        "relatorio_estoque_minimo.html",
        produtos=produtos,
        termo=termo,
        total_produtos=total_produtos,
        abaixo_minimo=abaixo_minimo,
        no_minimo=no_minimo,
        acima_minimo=acima_minimo,
        total_sugerido=total_sugerido,
    )


@app.route("/produtos/novo", methods=["POST"])
@login_required
def produtos_novo():
    config = get_empresa_configuracoes()
    codigo = request.form.get("codigo", "").strip()
    descricao = request.form.get("descricao", "").strip()
    familia_id_raw = request.form.get("familia_id", "").strip()
    unidade = request.form.get("unidade", "").strip()
    custo = request.form.get("custo", "0").strip() or "0"
    preco_venda = request.form.get("preco_venda", "0").strip() or "0"
    estoque_atual = request.form.get("estoque_atual", "0").strip() or "0"
    estoque_minimo = request.form.get("estoque_minimo", "0").strip() or "0"
    ativo = "ativo" in request.form
    categoria = ""
    familia_id = None

    if not descricao:
        flash("A descriÃ§Ã£o do produto Ã© obrigatÃ³ria.", "warning")
        return redirect(url_for("produtos"))
    if bool(config.get("produto_codigo_obrigatorio")) and not codigo:
        flash("CÃ³digo Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("produtos"))
    if bool(config.get("produto_unidade_obrigatoria")) and not unidade:
        flash("Unidade Ã© obrigatÃ³ria.", "warning")
        return redirect(url_for("produtos"))
    if bool(config.get("produto_custo_obrigatorio")) and not request.form.get("custo", "").strip():
        flash("Custo Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("produtos"))
    if bool(config.get("produto_preco_venda_obrigatorio")) and not request.form.get("preco_venda", "").strip():
        flash("PreÃ§o de venda Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("produtos"))
    if bool(config.get("produto_estoque_atual_obrigatorio")) and not request.form.get("estoque_atual", "").strip():
        flash("Estoque atual Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("produtos"))
    if bool(config.get("produto_estoque_minimo_obrigatorio")) and not request.form.get("estoque_minimo", "").strip():
        flash("Estoque mÃ­nimo Ã© obrigatÃ³rio.", "warning")
        return redirect(url_for("produtos"))

    if familia_id_raw:
        try:
            familia_id = int(familia_id_raw)
        except ValueError:
            flash("FamÃ­lia invÃ¡lida.", "warning")
            return redirect(url_for("produtos"))

        familia = fetch_one(
            """
            SELECT id, nome
            FROM public.familias_produtos
            WHERE id = %s
            """,
            (familia_id,),
        )
        if not familia:
            flash("FamÃ­lia nÃ£o encontrada.", "warning")
            return redirect(url_for("produtos"))
        categoria = familia["nome"]
    elif bool(config.get("produto_familia_obrigatoria")):
        flash("FamÃ­lia Ã© obrigatÃ³ria.", "warning")
        return redirect(url_for("produtos"))

    try:
        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO public.produtos (
                        codigo, descricao, categoria, familia_id, unidade,
                        custo, preco_venda, estoque_atual, estoque_minimo, ativo
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        codigo, descricao, categoria, familia_id, unidade,
                        custo, preco_venda, estoque_atual, estoque_minimo, ativo
                    ),
                )
                novo_produto_id = cur.fetchone()["id"]
                estoque_inicial = parse_decimal(estoque_atual)
                if estoque_inicial > 0:
                    registrar_movimentacao_estoque(
                        produto_id=novo_produto_id,
                        tipo_movimento="CADASTRO",
                        quantidade=estoque_inicial,
                        estoque_anterior=0,
                        estoque_posterior=estoque_inicial,
                        origem="PRODUTO",
                        referencia_id=novo_produto_id,
                        observacao="Estoque inicial informado no cadastro do produto.",
                        cur=cur,
                    )
            conn.commit()
        finally:
            conn.close()
        flash("Produto cadastrado com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar produto: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("produtos"))


@app.route("/familias-produtos/nova", methods=["POST"])
@login_required
def familias_produtos_nova():
    nome = request.form.get("nome", "").strip()
    ativo = "ativo" in request.form

    if not nome:
        flash("Informe o nome da famÃ­lia.", "warning")
        return redirect(url_for("familias_produtos"))

    try:
        execute(
            """
            INSERT INTO public.familias_produtos (nome, ativo)
            VALUES (%s, %s)
            """,
            (nome, ativo),
        )
        flash("FamÃ­lia cadastrada com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar famÃ­lia: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("familias_produtos"))


@app.route("/familias-produtos")
@login_required
def familias_produtos():
    familias = get_familias_produtos(apenas_ativas=False)
    return render_template("familias_produtos.html", familias=familias)


@app.route("/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
@login_required
def produto_editar(produto_id):
    produto = fetch_one(
        """
        SELECT
            p.id,
            p.codigo,
            p.descricao,
            p.categoria,
            p.familia_id,
            p.unidade,
            p.custo,
            p.preco_venda,
            p.estoque_atual,
            p.estoque_minimo,
            p.ativo,
            p.created_at
        FROM public.produtos p
        WHERE p.id = %s
        """,
        (produto_id,),
    )

    if not produto:
        flash("Produto não encontrado.", "warning")
        return redirect(url_for("produtos"))

    familias = get_familias_produtos(apenas_ativas=False)
    config = get_empresa_configuracoes()

    if request.method == "POST":
        config = get_empresa_configuracoes()
        codigo = request.form.get("codigo", "").strip()
        descricao = request.form.get("descricao", "").strip()
        familia_id_raw = request.form.get("familia_id", "").strip()
        unidade = request.form.get("unidade", "").strip()
        custo = request.form.get("custo", "0").strip() or "0"
        preco_venda = request.form.get("preco_venda", "0").strip() or "0"
        estoque_atual = request.form.get("estoque_atual", "0").strip() or "0"
        estoque_minimo = request.form.get("estoque_minimo", "0").strip() or "0"
        ativo = "ativo" in request.form
        categoria = ""
        familia_id = None
        estoque_atual_anterior = parse_decimal(produto.get("estoque_atual", 0))
        estoque_atual_novo = parse_decimal(estoque_atual)

        if not descricao:
            flash("A descrição do produto é obrigatória.", "warning")
            return render_template("produto_editar.html", produto=produto, familias=familias, config=config)
        if bool(config.get("produto_codigo_obrigatorio")) and not codigo:
            flash("Código é obrigatório.", "warning")
            return render_template("produto_editar.html", produto=produto, familias=familias, config=config)
        if bool(config.get("produto_unidade_obrigatoria")) and not unidade:
            flash("Unidade é obrigatória.", "warning")
            return render_template("produto_editar.html", produto=produto, familias=familias, config=config)
        if bool(config.get("produto_custo_obrigatorio")) and not request.form.get("custo", "").strip():
            flash("Custo é obrigatório.", "warning")
            return render_template("produto_editar.html", produto=produto, familias=familias, config=config)
        if bool(config.get("produto_preco_venda_obrigatorio")) and not request.form.get("preco_venda", "").strip():
            flash("Preço de venda é obrigatório.", "warning")
            return render_template("produto_editar.html", produto=produto, familias=familias, config=config)
        if bool(config.get("produto_estoque_atual_obrigatorio")) and not request.form.get("estoque_atual", "").strip():
            flash("Estoque atual é obrigatório.", "warning")
            return render_template("produto_editar.html", produto=produto, familias=familias, config=config)
        if bool(config.get("produto_estoque_minimo_obrigatorio")) and not request.form.get("estoque_minimo", "").strip():
            flash("Estoque mínimo é obrigatório.", "warning")
            return render_template("produto_editar.html", produto=produto, familias=familias, config=config)

        if familia_id_raw:
            try:
                familia_id = int(familia_id_raw)
            except ValueError:
                flash("Família inválida.", "warning")
                return render_template("produto_editar.html", produto=produto, familias=familias, config=config)

            familia = obter_familia_por_id(familia_id)
            if not familia:
                flash("Família não encontrada.", "warning")
                return render_template("produto_editar.html", produto=produto, familias=familias, config=config)
            categoria = familia["nome"]
        elif bool(config.get("produto_familia_obrigatoria")):
            flash("Família é obrigatória.", "warning")
            return render_template("produto_editar.html", produto=produto, familias=familias, config=config)

        try:
            execute(
                """
                UPDATE public.produtos
                   SET codigo = %s,
                       descricao = %s,
                       categoria = %s,
                       familia_id = %s,
                       unidade = %s,
                       custo = %s,
                       preco_venda = %s,
                       estoque_atual = %s,
                       estoque_minimo = %s,
                       ativo = %s
                 WHERE id = %s
                """,
                (
                    codigo,
                    descricao,
                    categoria,
                    familia_id,
                    unidade,
                    custo,
                    preco_venda,
                    estoque_atual,
                    estoque_minimo,
                    ativo,
                    produto_id,
                ),
            )
            diferenca_estoque = estoque_atual_novo - estoque_atual_anterior
            if abs(diferenca_estoque) > 0.000001:
                registrar_movimentacao_estoque(
                    produto_id=produto_id,
                    tipo_movimento="INVENTARIO",
                    quantidade=diferenca_estoque,
                    estoque_anterior=estoque_atual_anterior,
                    estoque_posterior=estoque_atual_novo,
                    origem="PRODUTO",
                    referencia_id=produto_id,
                    observacao="Inventario realizado pela edicao do produto.",
                )
            flash("Produto atualizado com sucesso.", "success")
            return redirect(url_for("produtos"))
        except psycopg2.Error as e:
            flash(f"Erro ao atualizar produto: {e.pgerror or str(e)}", "danger")
            produto.update(
                {
                    "codigo": codigo,
                    "descricao": descricao,
                    "categoria": categoria,
                    "familia_id": familia_id,
                    "unidade": unidade,
                    "custo": custo,
                    "preco_venda": preco_venda,
                    "estoque_atual": estoque_atual,
                    "estoque_minimo": estoque_minimo,
                    "ativo": ativo,
                }
            )

    return render_template("produto_editar.html", produto=produto, familias=familias, config=config)


@app.route("/produtos/<int:produto_id>/historico")
@login_required
def produto_historico(produto_id):
    produto = fetch_one(
        """
        SELECT
            p.id,
            p.codigo,
            p.descricao,
            COALESCE(f.nome, p.categoria) AS categoria,
            p.unidade,
            p.estoque_atual,
            p.estoque_minimo
        FROM public.produtos p
        LEFT JOIN public.familias_produtos f ON f.id = p.familia_id
        WHERE p.id = %s
        """,
        (produto_id,),
    )
    if not produto:
        flash("Produto nao encontrado.", "warning")
        return redirect(url_for("produtos"))

    movimentacoes = fetch_all(
        """
        SELECT
            m.id,
            m.tipo_movimento,
            m.origem,
            m.referencia_id,
            m.quantidade,
            m.estoque_anterior,
            m.estoque_posterior,
            m.observacao,
            m.created_at,
            u.nome AS usuario_nome,
            u.username AS usuario_username
        FROM public.movimentacoes_estoque m
        LEFT JOIN public.usuarios u ON u.id = m.usuario_id
        WHERE m.produto_id = %s
        ORDER BY m.created_at DESC, m.id DESC
        """,
        (produto_id,),
    )

    return render_template("produto_historico.html", produto=produto, movimentacoes=movimentacoes)


# =========================================================
# CONDIÃ‡Ã•ES DE PAGAMENTO
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
        flash("CondiÃ§Ã£o de pagamento cadastrada com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar condiÃ§Ã£o: {e.pgerror or str(e)}", "danger")

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
        flash("CondiÃ§Ã£o de pagamento atualizada com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao atualizar condiÃ§Ã£o: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("condicoes_pagamento"))


# =========================================================
# VENDAS
# =========================================================
@app.route("/vendas")
@login_required
def vendas():
    cliente_id_raw = (request.args.get("cliente_id") or "").strip()
    data_ini = (request.args.get("data_ini") or "").strip()
    data_fim = (request.args.get("data_fim") or "").strip()
    cliente_id = None
    cliente_nome = ""
    where = []
    params = []

    if cliente_id_raw:
        try:
            cliente_id = int(cliente_id_raw)
        except ValueError:
            cliente_id = None

    if cliente_id:
        where.append("v.cliente_id = %s")
        params.append(cliente_id)
        cliente = fetch_one(
            """
            SELECT nome
            FROM public.pessoas
            WHERE id = %s
            """,
            (cliente_id,),
        )
        cliente_nome = cliente["nome"] if cliente else ""
    if data_ini:
        where.append("DATE(v.data_venda) >= %s")
        params.append(data_ini)
    if data_fim:
        where.append("DATE(v.data_venda) <= %s")
        params.append(data_fim)

    tem_status_venda = column_exists("vendas", "status")
    if tem_status_venda:
        status_select = "COALESCE(v.status, 'ATIVA') AS status, v.estornada_em, v.estornada_por, v.motivo_estorno,"
        if not column_exists("vendas", "estornada_em"):
            status_select = "'ATIVA' AS status, NULL AS estornada_em, NULL AS estornada_por, NULL AS motivo_estorno,"
        where.append("COALESCE(v.status, 'ATIVA') <> 'EXCLUIDA'")
    else:
        status_select = "'ATIVA' AS status, NULL AS estornada_em, NULL AS estornada_por, NULL AS motivo_estorno,"

    query = f"""
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
            {status_select}
            p.nome AS cliente,
            u.nome AS usuario,
            cp.nome AS condicao_pagamento
        FROM public.vendas v
        LEFT JOIN public.pessoas p ON p.id = v.cliente_id
        LEFT JOIN public.usuarios u ON u.id = v.usuario_id
        LEFT JOIN public.condicoes_pagamento cp ON cp.id = v.condicao_pagamento_id
    """
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY v.id DESC"
    rows = fetch_all(query, tuple(params))

    clientes = fetch_all(
        """
        SELECT id, nome
        FROM public.pessoas
        WHERE ativo = TRUE
        ORDER BY nome
        """
    )
    usuario_logado = fetch_one(
        """
        SELECT COALESCE(permite_editar_venda, FALSE) AS permite_editar_venda,
               COALESCE(permite_estornar_venda, FALSE) AS permite_estornar_venda,
               COALESCE(permite_excluir_venda, FALSE) AS permite_excluir_venda
        FROM public.usuarios
        WHERE id = %s
        """,
        (session["user_id"],),
    ) or {"permite_editar_venda": False, "permite_estornar_venda": False, "permite_excluir_venda": False}

    return render_template(
        "vendas.html",
        vendas=rows,
        cliente_id=cliente_id,
        cliente_nome=cliente_nome,
        clientes=clientes,
        data_ini=data_ini,
        data_fim=data_fim,
        permite_editar_venda=bool(usuario_logado["permite_editar_venda"]),
        permite_estornar_venda=bool(usuario_logado["permite_estornar_venda"]),
        permite_excluir_venda=bool(usuario_logado["permite_excluir_venda"]),
    )


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

        empresa_config = get_empresa_configuracoes()
        permite_estoque_negativo = bool(empresa_config.get("permite_estoque_negativo"))
        bloqueia_desconto = bool(empresa_config.get("permite_desconto"))

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
                    flash(f"Produto {produto_id} nÃ£o encontrado.", "danger")
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
                    observacao_estoque = f"FicarÃ¡ com estoque negativo: {saldo_final:g}"

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
                flash("Adicione pelo menos um item Ã  venda.", "warning")
                return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

            if bloqueia_desconto and (float(desconto_itens_total) > 0 or float(desconto_informado) > 0):
                flash("Desconto está bloqueado nas configurações da empresa.", "warning")
                return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

            subtotal_liquido = max(float(valor_bruto) - float(desconto_itens_total), 0.0)
            if float(desconto_itens_total) > 0 and float(desconto_informado) > 0:
                flash("NÃ£o Ã© permitido aplicar desconto geral quando existir desconto em produto.", "warning")
                return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)
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
                    flash("Existe um lanÃ§amento de pagamento sem condiÃ§Ã£o de pagamento.", "warning")
                    return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

                valor_pag = parse_decimal(valor_raw or "0")
                if valor_pag <= 0:
                    flash("Existe um lanÃ§amento de pagamento com valor invÃ¡lido.", "warning")
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
                    flash("Uma das condiÃ§Ãµes de pagamento informadas Ã© invÃ¡lida.", "danger")
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
                flash("Adicione pelo menos um lanÃ§amento de pagamento.", "warning")
                return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

            if round(total_pagamentos, 2) != round(valor_liquido, 2):
                flash(f"Os pagamentos lanÃ§ados somam R$ {total_pagamentos:.2f} e a venda totaliza R$ {valor_liquido:.2f}. Ajuste antes de finalizar.", "danger")
                return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)

            condicao_principal = pagamentos[0]
            condicoes_ids_texto = ",".join(str(p["condicao_id"]) for p in pagamentos)
            total_parcelas = sum(int(p["parcelas"]) for p in pagamentos) or 1
            valor_parcela_base = float(valor_liquido) / total_parcelas if total_parcelas > 0 else float(valor_liquido)

            conn = get_conn()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    venda_itens_tem_desconto_tipo = column_exists("venda_itens", "desconto_tipo")
                    venda_itens_tem_desconto_valor = column_exists("venda_itens", "desconto_valor")
                    venda_itens_tem_desconto_total = column_exists("venda_itens", "desconto_total")
                    venda_itens_tem_total_bruto = column_exists("venda_itens", "total_bruto")
                    venda_itens_tem_total_liquido = column_exists("venda_itens", "total_liquido")
                    venda_itens_tem_observacao_estoque = column_exists("venda_itens", "observacao_estoque")

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
                        colunas_item = [
                            "venda_id",
                            "produto_id",
                            "quantidade",
                            "valor_unitario",
                        ]
                        valores_item = [
                            venda_id,
                            item["produto_id"],
                            item["quantidade"],
                            item["valor_unitario"],
                        ]

                        if venda_itens_tem_desconto_tipo:
                            colunas_item.append("desconto_tipo")
                            valores_item.append(item["desconto_tipo"])
                        if venda_itens_tem_desconto_valor:
                            colunas_item.append("desconto_valor")
                            valores_item.append(item["desconto_valor"])
                        if venda_itens_tem_desconto_total:
                            colunas_item.append("desconto_total")
                            valores_item.append(item["desconto_total"])
                        if venda_itens_tem_total_bruto:
                            colunas_item.append("total_bruto")
                            valores_item.append(item["total_bruto"])
                        if venda_itens_tem_total_liquido:
                            colunas_item.append("total_liquido")
                            valores_item.append(item["total"])

                        colunas_item.append("total")
                        valores_item.append(item["total"])

                        if venda_itens_tem_observacao_estoque:
                            colunas_item.append("observacao_estoque")
                            valores_item.append(item["observacao_estoque"])

                        placeholders_item = ", ".join(["%s"] * len(valores_item))
                        sql_item = f"""
                            INSERT INTO public.venda_itens (
                                {", ".join(colunas_item)}
                            )
                            VALUES ({placeholders_item})
                        """
                        cur.execute(sql_item, tuple(valores_item))

                        cur.execute(
                            """
                            UPDATE public.produtos
                               SET estoque_atual = COALESCE(estoque_atual, 0) - %s
                             WHERE id = %s
                            """,
                            (item["quantidade"], item["produto_id"]),
                        )
                        registrar_movimentacao_estoque(
                            produto_id=item["produto_id"],
                            tipo_movimento="VENDA",
                            quantidade=-float(item["quantidade"] or 0),
                            estoque_anterior=float(produto_db["estoque_atual"] or 0),
                            estoque_posterior=float(produto_db["estoque_atual"] or 0) - float(item["quantidade"] or 0),
                            origem="VENDA",
                            referencia_id=venda_id,
                            observacao=f"Baixa de estoque pela venda #{venda_id}.",
                            cur=cur,
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
            flash("Valores invÃ¡lidos na venda.", "danger")

    return render_template("venda_nova.html", clientes=clientes, produtos=produtos, condicoes=condicoes)


@app.route("/vendas/<int:venda_id>/editar", methods=["GET", "POST"])
@login_required
def vendas_editar(venda_id):
    usuario = fetch_one(
        """
        SELECT COALESCE(permite_editar_venda, FALSE) AS permite_editar_venda
        FROM public.usuarios
        WHERE id = %s
        """,
        (session["user_id"],),
    )
    if not usuario or not usuario["permite_editar_venda"]:
        flash("Você não tem permissão para editar vendas.", "danger")
        return redirect(url_for("vendas"))

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
        SELECT id, nome, forma_pagamento, parcelas, dias_intervalo, taxa_percentual
        FROM public.condicoes_pagamento
        WHERE ativo = TRUE
        ORDER BY nome
        """
    )

    venda_tem_status = column_exists("vendas", "status")
    venda_tem_desconto_tipo = column_exists("vendas", "desconto_tipo")
    item_tem_desconto_tipo = column_exists("venda_itens", "desconto_tipo")
    item_tem_desconto_valor = column_exists("venda_itens", "desconto_valor")
    pagamento_tem_status = column_exists("vendas_pagamentos", "status")
    vencimento_tem_pagamento_id = column_exists("vendas_vencimentos", "pagamento_id")

    status_select = "COALESCE(status, 'ATIVA') AS status" if venda_tem_status else "'ATIVA' AS status"
    desconto_tipo_select = "COALESCE(desconto_tipo, 'valor') AS desconto_tipo" if venda_tem_desconto_tipo else "'valor' AS desconto_tipo"

    venda = fetch_one(
        f"""
        SELECT
            id,
            cliente_id,
            observacoes,
            desconto,
            valor_bruto,
            valor_liquido,
            forma_pagamento,
            parcelas,
            valor_parcela,
            condicao_pagamento_id,
            {desconto_tipo_select},
            {status_select}
        FROM public.vendas
        WHERE id = %s
        """,
        (venda_id,),
    )
    if not venda:
        flash("Venda não encontrada.", "warning")
        return redirect(url_for("vendas"))
    if venda["status"] != "ESTORNADA":
        flash("Para editar uma venda, ela precisa estar estornada primeiro.", "warning")
        return redirect(url_for("vendas"))

    item_select_desconto_tipo = "COALESCE(vi.desconto_tipo, 'valor') AS desconto_tipo," if item_tem_desconto_tipo else "'valor' AS desconto_tipo,"
    item_select_desconto_valor = "COALESCE(vi.desconto_valor, 0) AS desconto_valor," if item_tem_desconto_valor else "0 AS desconto_valor,"
    itens_originais = fetch_all(
        f"""
        SELECT
            vi.produto_id,
            vi.quantidade,
            vi.valor_unitario,
            {item_select_desconto_tipo}
            {item_select_desconto_valor}
            p.descricao
        FROM public.venda_itens vi
        LEFT JOIN public.produtos p ON p.id = vi.produto_id
        WHERE vi.venda_id = %s
        ORDER BY vi.id
        """,
        (venda_id,),
    )
    pagamentos_originais = fetch_all(
        """
        SELECT condicao_pagamento_id AS condicao_id, valor
        FROM public.vendas_pagamentos
        WHERE venda_id = %s
        ORDER BY id
        """,
        (venda_id,),
    )
    if not pagamentos_originais and venda.get("condicao_pagamento_id"):
        pagamentos_originais = [{"condicao_id": venda["condicao_pagamento_id"], "valor": float(venda["valor_liquido"] or 0)}]

    estoque_original_map = {}
    for item in itens_originais:
        produto_id = int(item["produto_id"])
        estoque_original_map[str(produto_id)] = float(estoque_original_map.get(str(produto_id), 0)) + float(item["quantidade"] or 0)

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
        empresa_config = get_empresa_configuracoes()
        permite_estoque_negativo = bool(empresa_config.get("permite_estoque_negativo"))
        bloqueia_desconto = bool(empresa_config.get("permite_desconto"))

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
                    return redirect(url_for("vendas_editar", venda_id=venda_id))

                quantidade = parse_quantity_by_unidade(produto_db["unidade"], quantidades[i] if i < len(quantidades) else "0")
                valor_unitario = parse_decimal(valores[i] if i < len(valores) else "0")
                if quantidade <= 0:
                    continue

                estoque_disponivel = float(produto_db["estoque_atual"] or 0) + float(estoque_original_map.get(str(produto_id), 0))
                if (not permite_estoque_negativo) and estoque_disponivel < float(quantidade):
                    flash(f"Estoque insuficiente para {produto_db['descricao']}.", "danger")
                    return redirect(url_for("vendas_editar", venda_id=venda_id))

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
                saldo_final = estoque_disponivel - float(quantidade)
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
                return redirect(url_for("vendas_editar", venda_id=venda_id))

            if bloqueia_desconto and (float(desconto_itens_total) > 0 or float(desconto_informado) > 0):
                flash("Desconto está bloqueado nas configurações da empresa.", "warning")
                return redirect(url_for("vendas_editar", venda_id=venda_id))

            subtotal_liquido = max(float(valor_bruto) - float(desconto_itens_total), 0.0)
            if float(desconto_itens_total) > 0 and float(desconto_informado) > 0:
                flash("Não é permitido aplicar desconto geral quando existir desconto em produto.", "warning")
                return redirect(url_for("vendas_editar", venda_id=venda_id))

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
                    return redirect(url_for("vendas_editar", venda_id=venda_id))

                valor_pag = parse_decimal(valor_raw or "0")
                if valor_pag <= 0:
                    flash("Existe um lançamento de pagamento com valor inválido.", "warning")
                    return redirect(url_for("vendas_editar", venda_id=venda_id))

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
                    return redirect(url_for("vendas_editar", venda_id=venda_id))

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
                return redirect(url_for("vendas_editar", venda_id=venda_id))

            if round(total_pagamentos, 2) != round(valor_liquido, 2):
                flash(f"Os pagamentos lançados somam R$ {total_pagamentos:.2f} e a venda totaliza R$ {valor_liquido:.2f}. Ajuste antes de finalizar.", "danger")
                return redirect(url_for("vendas_editar", venda_id=venda_id))

            condicao_principal = pagamentos[0]
            condicoes_ids_texto = ",".join(str(p["condicao_id"]) for p in pagamentos)
            total_parcelas = sum(int(p["parcelas"]) for p in pagamentos) or 1
            valor_parcela_base = float(valor_liquido) / total_parcelas if total_parcelas > 0 else float(valor_liquido)

            conn = get_conn()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    venda_itens_tem_desconto_tipo = column_exists("venda_itens", "desconto_tipo")
                    venda_itens_tem_desconto_valor = column_exists("venda_itens", "desconto_valor")
                    venda_itens_tem_desconto_total = column_exists("venda_itens", "desconto_total")
                    venda_itens_tem_total_bruto = column_exists("venda_itens", "total_bruto")
                    venda_itens_tem_total_liquido = column_exists("venda_itens", "total_liquido")
                    venda_itens_tem_observacao_estoque = column_exists("venda_itens", "observacao_estoque")

                    cur.execute("DELETE FROM public.vendas_vencimentos WHERE venda_id = %s", (venda_id,))
                    cur.execute("DELETE FROM public.vendas_pagamentos WHERE venda_id = %s", (venda_id,))
                    cur.execute("DELETE FROM public.venda_itens WHERE venda_id = %s", (venda_id,))

                    cur.execute(
                        """
                        UPDATE public.vendas
                           SET cliente_id = %s,
                               valor_bruto = %s,
                               desconto = %s,
                               desconto_tipo = %s,
                               desconto_itens_total = %s,
                               desconto_geral_valor = %s,
                               acrescimo = %s,
                               valor_liquido = %s,
                               forma_pagamento = %s,
                               parcelas = %s,
                               valor_parcela = %s,
                               condicao_pagamento_id = %s,
                               observacoes = %s,
                               condicoes_pagamento_ids = %s
                         WHERE id = %s
                        """,
                        (
                            cliente_id if cliente_id else None,
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
                            venda_id,
                        ),
                    )

                    for item in itens:
                        colunas_item = ["venda_id", "produto_id", "quantidade", "valor_unitario"]
                        valores_item = [venda_id, item["produto_id"], item["quantidade"], item["valor_unitario"]]

                        if venda_itens_tem_desconto_tipo:
                            colunas_item.append("desconto_tipo")
                            valores_item.append(item["desconto_tipo"])
                        if venda_itens_tem_desconto_valor:
                            colunas_item.append("desconto_valor")
                            valores_item.append(item["desconto_valor"])
                        if venda_itens_tem_desconto_total:
                            colunas_item.append("desconto_total")
                            valores_item.append(item["desconto_total"])
                        if venda_itens_tem_total_bruto:
                            colunas_item.append("total_bruto")
                            valores_item.append(item["total_bruto"])
                        if venda_itens_tem_total_liquido:
                            colunas_item.append("total_liquido")
                            valores_item.append(item["total"])

                        colunas_item.append("total")
                        valores_item.append(item["total"])

                        if venda_itens_tem_observacao_estoque:
                            colunas_item.append("observacao_estoque")
                            valores_item.append(item["observacao_estoque"])

                        placeholders_item = ", ".join(["%s"] * len(valores_item))
                        cur.execute(
                            f"""
                            INSERT INTO public.venda_itens ({", ".join(colunas_item)})
                            VALUES ({placeholders_item})
                            """,
                            tuple(valores_item),
                        )

                    data_base = date.today()
                    for pagamento in pagamentos:
                        status_pagamento_select = ", status" if pagamento_tem_status else ""
                        status_pagamento_values = ", %s" if pagamento_tem_status else ""
                        pagamento_params = [
                            venda_id,
                            pagamento["condicao_id"],
                            pagamento["nome"],
                            pagamento["forma_pagamento"],
                            pagamento["valor"],
                            pagamento["parcelas"],
                            pagamento["dias_intervalo"],
                        ]
                        if pagamento_tem_status:
                            pagamento_params.append("PENDENTE")

                        cur.execute(
                            f"""
                            INSERT INTO public.vendas_pagamentos (
                                venda_id,
                                condicao_pagamento_id,
                                descricao_condicao,
                                forma_pagamento,
                                valor,
                                parcelas,
                                dias_intervalo
                                {status_pagamento_select}
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s{status_pagamento_values})
                            RETURNING id
                            """,
                            tuple(pagamento_params),
                        )
                        pagamento_id = cur.fetchone()["id"]
                        valor_parcela = float(pagamento["valor"]) / int(pagamento["parcelas"] or 1)

                        for numero in range(1, int(pagamento["parcelas"] or 1) + 1):
                            data_vencimento = data_base if int(pagamento["parcelas"] or 1) == 1 else data_base + timedelta(days=(numero - 1) * int(pagamento["dias_intervalo"] or 30))
                            if vencimento_tem_pagamento_id:
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
                                    (venda_id, pagamento_id, numero, round(valor_parcela, 2), data_vencimento, "PENDENTE"),
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
                                    (venda_id, numero, round(valor_parcela, 2), data_vencimento, "PENDENTE"),
                                )

                conn.commit()
                flash("Venda atualizada com sucesso.", "success")
                return redirect(url_for("vendas"))
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except ValueError:
            flash("Valores inválidos na venda.", "danger")
            return redirect(url_for("vendas_editar", venda_id=venda_id))

    initial_venda = {
        "cliente_id": venda["cliente_id"],
        "observacoes": venda["observacoes"] or "",
        "desconto_tipo": venda["desconto_tipo"] or "valor",
        "desconto": float(venda["desconto"] or 0),
        "itens": [
            {
                "produto_id": item["produto_id"],
                "quantidade": float(item["quantidade"] or 0),
                "valor_unitario": float(item["valor_unitario"] or 0),
                "desconto_tipo": item["desconto_tipo"] or "valor",
                "desconto_valor": float(item["desconto_valor"] or 0),
            }
            for item in itens_originais
        ],
        "pagamentos": [
            {
                "condicao_id": pagamento["condicao_id"],
                "valor": float(pagamento["valor"] or 0),
            }
            for pagamento in pagamentos_originais
        ],
    }

    return render_template(
        "venda_editar.html",
        venda=venda,
        clientes=clientes,
        produtos=produtos,
        condicoes=condicoes,
        initial_venda=initial_venda,
        estoque_original_map=estoque_original_map,
    )


@app.route("/vendas/<int:venda_id>/estornar", methods=["POST"])
@login_required
def vendas_estornar(venda_id):
    usuario = fetch_one(
        """
        SELECT COALESCE(permite_estornar_venda, FALSE) AS permite_estornar_venda
        FROM public.usuarios
        WHERE id = %s
        """,
        (session["user_id"],),
    )
    if not usuario or not usuario["permite_estornar_venda"]:
        flash("Você não tem permissão para estornar vendas.", "danger")
        return redirect(url_for("vendas"))

    if not column_exists("vendas", "status"):
        flash("O banco ainda não possui as colunas de estorno. Rode o SQL de atualização.", "warning")
        return redirect(url_for("vendas"))

    venda = fetch_one(
        """
        SELECT id, COALESCE(status, 'ATIVA') AS status
        FROM public.vendas
        WHERE id = %s
        """,
        (venda_id,),
    )
    if not venda:
        flash("Venda não encontrada.", "warning")
        return redirect(url_for("vendas"))
    if venda["status"] == "ESTORNADA":
        flash("Esta venda já está estornada.", "info")
        return redirect(url_for("vendas"))

    motivo = (request.form.get("motivo_estorno") or "").strip() or "Estorno manual"

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT produto_id, quantidade
                FROM public.venda_itens
                WHERE venda_id = %s
                """,
                (venda_id,),
            )
            itens = cur.fetchall()

            for item in itens:
                produto_atual = fetch_one(
                    """
                    SELECT COALESCE(estoque_atual, 0) AS estoque_atual
                    FROM public.produtos
                    WHERE id = %s
                    """,
                    (item["produto_id"],),
                ) or {"estoque_atual": 0}
                cur.execute(
                    """
                    UPDATE public.produtos
                       SET estoque_atual = COALESCE(estoque_atual, 0) + %s
                     WHERE id = %s
                    """,
                    (item["quantidade"], item["produto_id"]),
                )
                registrar_movimentacao_estoque(
                    produto_id=item["produto_id"],
                    tipo_movimento="ESTORNO_VENDA",
                    quantidade=float(item["quantidade"] or 0),
                    estoque_anterior=float(produto_atual["estoque_atual"] or 0),
                    estoque_posterior=float(produto_atual["estoque_atual"] or 0) + float(item["quantidade"] or 0),
                    origem="VENDA",
                    referencia_id=venda_id,
                    observacao=f"Devolucao de estoque pelo estorno da venda #{venda_id}.",
                    cur=cur,
                )

            cur.execute(
                """
                UPDATE public.vendas
                   SET status = 'ESTORNADA',
                       estornada_em = NOW(),
                       estornada_por = %s,
                       motivo_estorno = %s
                 WHERE id = %s
                """,
                (session["user_id"], motivo, venda_id),
            )

            if column_exists("vendas_pagamentos", "status"):
                cur.execute(
                    """
                    UPDATE public.vendas_pagamentos
                       SET status = 'ESTORNADO'
                     WHERE venda_id = %s
                    """,
                    (venda_id,),
                )

            cur.execute(
                """
                UPDATE public.vendas_vencimentos
                   SET status = 'ESTORNADO'
                 WHERE venda_id = %s
                """,
                (venda_id,),
            )

        conn.commit()
        flash("Venda estornada com sucesso.", "success")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return redirect(url_for("vendas"))


@app.route("/vendas/<int:venda_id>/excluir", methods=["POST"])
@login_required
def vendas_excluir(venda_id):
    usuario = fetch_one(
        """
        SELECT COALESCE(permite_excluir_venda, FALSE) AS permite_excluir_venda
        FROM public.usuarios
        WHERE id = %s
        """,
        (session["user_id"],),
    )
    if not usuario or not usuario["permite_excluir_venda"]:
        flash("Você não tem permissão para excluir vendas.", "danger")
        return redirect(url_for("vendas"))

    tem_status_venda = column_exists("vendas", "status")
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if tem_status_venda:
                cur.execute(
                    """
                    SELECT id, COALESCE(status, 'ATIVA') AS status
                    FROM public.vendas
                    WHERE id = %s
                    """,
                    (venda_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, 'ATIVA' AS status
                    FROM public.vendas
                    WHERE id = %s
                    """,
                    (venda_id,),
                )
            venda = cur.fetchone()

            if not venda:
                flash("Venda não encontrada.", "warning")
                conn.rollback()
                return redirect(url_for("vendas"))

            if tem_status_venda and venda["status"] != "ESTORNADA":
                flash("Para excluir uma venda, ela precisa estar estornada primeiro.", "warning")
                conn.rollback()
                return redirect(url_for("vendas"))

            cur.execute(
                """
                SELECT produto_id, quantidade
                FROM public.venda_itens
                WHERE venda_id = %s
                """,
                (venda_id,),
            )
            itens = cur.fetchall()

            cur.execute("DELETE FROM public.vendas_vencimentos WHERE venda_id = %s", (venda_id,))
            cur.execute("DELETE FROM public.vendas_pagamentos WHERE venda_id = %s", (venda_id,))
            cur.execute("DELETE FROM public.venda_itens WHERE venda_id = %s", (venda_id,))
            cur.execute("DELETE FROM public.vendas WHERE id = %s", (venda_id,))

        conn.commit()
        flash("Venda excluída com sucesso.", "success")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return redirect(url_for("vendas"))

@app.route("/empresas")
@login_required
def empresas():
    rows = fetch_all(
        """
        SELECT
            id, nome_fantasia, razao_social, cnpj, telefone, email,
            cidade, estado, responsavel,
            COALESCE(permite_estoque_negativo, FALSE) AS permite_estoque_negativo,
            COALESCE(permite_desconto, FALSE) AS permite_desconto,
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
        tem_logo_path = column_exists("empresas", "logo_path")
        logo_file = request.files.get("logo")
        nome_fantasia = request.form.get("nome_fantasia", "").strip()
        razao_social = request.form.get("razao_social", "").strip()
        cnpj = request.form.get("cnpj", "").strip()
        telefone = request.form.get("telefone", "").strip()
        email = request.form.get("email", "").strip()
        cidade = request.form.get("cidade", "").strip()
        estado = request.form.get("estado", "").strip().upper()
        responsavel = request.form.get("responsavel", "").strip()
        produto_codigo_obrigatorio = "produto_codigo_obrigatorio" in request.form
        produto_familia_obrigatoria = "produto_familia_obrigatoria" in request.form
        produto_unidade_obrigatoria = "produto_unidade_obrigatoria" in request.form
        produto_custo_obrigatorio = "produto_custo_obrigatorio" in request.form
        produto_preco_venda_obrigatorio = "produto_preco_venda_obrigatorio" in request.form
        produto_estoque_atual_obrigatorio = "produto_estoque_atual_obrigatorio" in request.form
        produto_estoque_minimo_obrigatorio = "produto_estoque_minimo_obrigatorio" in request.form
        cliente_tipo_obrigatorio = "cliente_tipo_obrigatorio" in request.form
        cliente_nome_obrigatorio = "cliente_nome_obrigatorio" in request.form
        cliente_documento_obrigatorio = "cliente_documento_obrigatorio" in request.form
        cliente_telefone_obrigatorio = "cliente_telefone_obrigatorio" in request.form
        cliente_email_obrigatorio = "cliente_email_obrigatorio" in request.form
        cliente_cep_obrigatorio = "cliente_cep_obrigatorio" in request.form
        cliente_endereco_obrigatorio = "cliente_endereco_obrigatorio" in request.form
        cliente_cidade_obrigatorio = "cliente_cidade_obrigatorio" in request.form
        cliente_estado_obrigatorio = "cliente_estado_obrigatorio" in request.form
        cliente_observacoes_obrigatorio = "cliente_observacoes_obrigatorio" in request.form
        permite_estoque_negativo = "permite_estoque_negativo" in request.form
        permite_desconto = "permite_desconto" in request.form
        ativo = "ativo" in request.form

        if not nome_fantasia:
            flash("Informe o nome fantasia da empresa.", "warning")
            return render_template("empresa_form.html", empresa=None)
        if logo_file and logo_file.filename and not logo_jpeg_valido(logo_file):
            flash("O logo deve ser um arquivo JPEG (.jpg ou .jpeg).", "warning")
            return render_template("empresa_form.html", empresa=None)

        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO public.empresas (
                        nome_fantasia, razao_social, cnpj, telefone, email,
                        cidade, estado, responsavel,
                        produto_codigo_obrigatorio, produto_familia_obrigatoria, produto_unidade_obrigatoria,
                        produto_custo_obrigatorio, produto_preco_venda_obrigatorio, produto_estoque_atual_obrigatorio,
                        produto_estoque_minimo_obrigatorio,
                        cliente_tipo_obrigatorio, cliente_nome_obrigatorio, cliente_documento_obrigatorio,
                        cliente_telefone_obrigatorio, cliente_email_obrigatorio, cliente_cep_obrigatorio,
                        cliente_endereco_obrigatorio, cliente_cidade_obrigatorio, cliente_estado_obrigatorio,
                        cliente_observacoes_obrigatorio,
                        permite_estoque_negativo, permite_desconto, ativo
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        nome_fantasia, razao_social, cnpj, telefone, email,
                        cidade, estado, responsavel,
                        produto_codigo_obrigatorio, produto_familia_obrigatoria, produto_unidade_obrigatoria,
                        produto_custo_obrigatorio, produto_preco_venda_obrigatorio, produto_estoque_atual_obrigatorio,
                        produto_estoque_minimo_obrigatorio,
                        cliente_tipo_obrigatorio, cliente_nome_obrigatorio, cliente_documento_obrigatorio,
                        cliente_telefone_obrigatorio, cliente_email_obrigatorio, cliente_cep_obrigatorio,
                        cliente_endereco_obrigatorio, cliente_cidade_obrigatorio, cliente_estado_obrigatorio,
                        cliente_observacoes_obrigatorio,
                        permite_estoque_negativo, permite_desconto, ativo
                    ),
                )
                empresa_id = cur.fetchone()["id"]

                if logo_file and logo_file.filename and tem_logo_path:
                    logo_path = salvar_logo_empresa(logo_file, empresa_id)
                    cur.execute(
                        "UPDATE public.empresas SET logo_path = %s WHERE id = %s",
                        (logo_path, empresa_id),
                    )
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "warning")
            return render_template("empresa_form.html", empresa=None)
        finally:
            conn.close()

        if logo_file and logo_file.filename and not tem_logo_path:
            flash("Empresa cadastrada, mas a coluna do logo ainda não existe no banco. Rode o SQL manual para habilitar o logo.", "warning")
            return redirect(url_for("empresas"))

        flash("Empresa cadastrada com sucesso.", "success")
        return redirect(url_for("empresas"))

    return render_template("empresa_form.html", empresa=None)


@app.route("/empresas/<int:empresa_id>/editar", methods=["GET", "POST"])
@login_required
def empresas_editar(empresa_id):
    logo_select = "logo_path," if column_exists("empresas", "logo_path") else "NULL AS logo_path,"
    empresa = fetch_one(
        f"""
        SELECT
            id, nome_fantasia, razao_social, cnpj, telefone, email,
            cidade, estado, responsavel,
            {logo_select}
            COALESCE(produto_codigo_obrigatorio, FALSE) AS produto_codigo_obrigatorio,
            COALESCE(produto_familia_obrigatoria, FALSE) AS produto_familia_obrigatoria,
            COALESCE(produto_unidade_obrigatoria, FALSE) AS produto_unidade_obrigatoria,
            COALESCE(produto_custo_obrigatorio, FALSE) AS produto_custo_obrigatorio,
            COALESCE(produto_preco_venda_obrigatorio, FALSE) AS produto_preco_venda_obrigatorio,
            COALESCE(produto_estoque_atual_obrigatorio, FALSE) AS produto_estoque_atual_obrigatorio,
            COALESCE(produto_estoque_minimo_obrigatorio, FALSE) AS produto_estoque_minimo_obrigatorio,
            COALESCE(cliente_tipo_obrigatorio, FALSE) AS cliente_tipo_obrigatorio,
            COALESCE(cliente_nome_obrigatorio, FALSE) AS cliente_nome_obrigatorio,
            COALESCE(cliente_documento_obrigatorio, FALSE) AS cliente_documento_obrigatorio,
            COALESCE(cliente_telefone_obrigatorio, FALSE) AS cliente_telefone_obrigatorio,
            COALESCE(cliente_email_obrigatorio, FALSE) AS cliente_email_obrigatorio,
            COALESCE(cliente_cep_obrigatorio, FALSE) AS cliente_cep_obrigatorio,
            COALESCE(cliente_endereco_obrigatorio, FALSE) AS cliente_endereco_obrigatorio,
            COALESCE(cliente_cidade_obrigatorio, FALSE) AS cliente_cidade_obrigatorio,
            COALESCE(cliente_estado_obrigatorio, FALSE) AS cliente_estado_obrigatorio,
            COALESCE(cliente_observacoes_obrigatorio, FALSE) AS cliente_observacoes_obrigatorio,
            COALESCE(permite_estoque_negativo, FALSE) AS permite_estoque_negativo,
            COALESCE(permite_desconto, FALSE) AS permite_desconto,
            ativo, created_at
        FROM public.empresas
        WHERE id = %s
        """,
        (empresa_id,),
    )

    if not empresa:
        flash("Empresa nÃ£o encontrada.", "warning")
        return redirect(url_for("empresas"))

    if request.method == "POST":
        tem_logo_path = column_exists("empresas", "logo_path")
        logo_file = request.files.get("logo")
        nome_fantasia = request.form.get("nome_fantasia", "").strip()
        razao_social = request.form.get("razao_social", "").strip()
        cnpj = request.form.get("cnpj", "").strip()
        telefone = request.form.get("telefone", "").strip()
        email = request.form.get("email", "").strip()
        cidade = request.form.get("cidade", "").strip()
        estado = request.form.get("estado", "").strip().upper()
        responsavel = request.form.get("responsavel", "").strip()
        produto_codigo_obrigatorio = "produto_codigo_obrigatorio" in request.form
        produto_familia_obrigatoria = "produto_familia_obrigatoria" in request.form
        produto_unidade_obrigatoria = "produto_unidade_obrigatoria" in request.form
        produto_custo_obrigatorio = "produto_custo_obrigatorio" in request.form
        produto_preco_venda_obrigatorio = "produto_preco_venda_obrigatorio" in request.form
        produto_estoque_atual_obrigatorio = "produto_estoque_atual_obrigatorio" in request.form
        produto_estoque_minimo_obrigatorio = "produto_estoque_minimo_obrigatorio" in request.form
        cliente_tipo_obrigatorio = "cliente_tipo_obrigatorio" in request.form
        cliente_nome_obrigatorio = "cliente_nome_obrigatorio" in request.form
        cliente_documento_obrigatorio = "cliente_documento_obrigatorio" in request.form
        cliente_telefone_obrigatorio = "cliente_telefone_obrigatorio" in request.form
        cliente_email_obrigatorio = "cliente_email_obrigatorio" in request.form
        cliente_cep_obrigatorio = "cliente_cep_obrigatorio" in request.form
        cliente_endereco_obrigatorio = "cliente_endereco_obrigatorio" in request.form
        cliente_cidade_obrigatorio = "cliente_cidade_obrigatorio" in request.form
        cliente_estado_obrigatorio = "cliente_estado_obrigatorio" in request.form
        cliente_observacoes_obrigatorio = "cliente_observacoes_obrigatorio" in request.form
        permite_estoque_negativo = "permite_estoque_negativo" in request.form
        permite_desconto = "permite_desconto" in request.form
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
                "produto_codigo_obrigatorio": produto_codigo_obrigatorio,
                "produto_familia_obrigatoria": produto_familia_obrigatoria,
                "produto_unidade_obrigatoria": produto_unidade_obrigatoria,
                "produto_custo_obrigatorio": produto_custo_obrigatorio,
                "produto_preco_venda_obrigatorio": produto_preco_venda_obrigatorio,
                "produto_estoque_atual_obrigatorio": produto_estoque_atual_obrigatorio,
                "produto_estoque_minimo_obrigatorio": produto_estoque_minimo_obrigatorio,
                "cliente_tipo_obrigatorio": cliente_tipo_obrigatorio,
                "cliente_nome_obrigatorio": cliente_nome_obrigatorio,
                "cliente_documento_obrigatorio": cliente_documento_obrigatorio,
                "cliente_telefone_obrigatorio": cliente_telefone_obrigatorio,
                "cliente_email_obrigatorio": cliente_email_obrigatorio,
                "cliente_cep_obrigatorio": cliente_cep_obrigatorio,
                "cliente_endereco_obrigatorio": cliente_endereco_obrigatorio,
                "cliente_cidade_obrigatorio": cliente_cidade_obrigatorio,
                "cliente_estado_obrigatorio": cliente_estado_obrigatorio,
                "cliente_observacoes_obrigatorio": cliente_observacoes_obrigatorio,
                "permite_estoque_negativo": permite_estoque_negativo,
                "permite_desconto": permite_desconto,
                "ativo": ativo,
            })
            return render_template("empresa_form.html", empresa=empresa)

        if logo_file and logo_file.filename and not logo_jpeg_valido(logo_file):
            flash("O logo deve ser um arquivo JPEG (.jpg ou .jpeg).", "warning")
            empresa.update({
                "nome_fantasia": nome_fantasia,
                "razao_social": razao_social,
                "cnpj": cnpj,
                "telefone": telefone,
                "email": email,
                "cidade": cidade,
                "estado": estado,
                "responsavel": responsavel,
            })
            return render_template("empresa_form.html", empresa=empresa)

        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
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
                           produto_codigo_obrigatorio = %s,
                           produto_familia_obrigatoria = %s,
                           produto_unidade_obrigatoria = %s,
                           produto_custo_obrigatorio = %s,
                           produto_preco_venda_obrigatorio = %s,
                           produto_estoque_atual_obrigatorio = %s,
                           produto_estoque_minimo_obrigatorio = %s,
                           cliente_tipo_obrigatorio = %s,
                           cliente_nome_obrigatorio = %s,
                           cliente_documento_obrigatorio = %s,
                           cliente_telefone_obrigatorio = %s,
                           cliente_email_obrigatorio = %s,
                           cliente_cep_obrigatorio = %s,
                           cliente_endereco_obrigatorio = %s,
                           cliente_cidade_obrigatorio = %s,
                           cliente_estado_obrigatorio = %s,
                           cliente_observacoes_obrigatorio = %s,
                           permite_estoque_negativo = %s,
                           permite_desconto = %s,
                           ativo = %s
                     WHERE id = %s
                    """,
                    (
                        nome_fantasia, razao_social, cnpj, telefone, email,
                        cidade, estado, responsavel,
                        produto_codigo_obrigatorio,
                        produto_familia_obrigatoria,
                        produto_unidade_obrigatoria,
                        produto_custo_obrigatorio,
                        produto_preco_venda_obrigatorio,
                        produto_estoque_atual_obrigatorio,
                        produto_estoque_minimo_obrigatorio,
                        cliente_tipo_obrigatorio,
                        cliente_nome_obrigatorio,
                        cliente_documento_obrigatorio,
                        cliente_telefone_obrigatorio,
                        cliente_email_obrigatorio,
                        cliente_cep_obrigatorio,
                        cliente_endereco_obrigatorio,
                        cliente_cidade_obrigatorio,
                        cliente_estado_obrigatorio,
                        cliente_observacoes_obrigatorio,
                        permite_estoque_negativo,
                        permite_desconto,
                        ativo,
                        empresa_id
                    ),
                )
                if logo_file and logo_file.filename and tem_logo_path:
                    logo_path = salvar_logo_empresa(logo_file, empresa_id)
                    cur.execute(
                        "UPDATE public.empresas SET logo_path = %s WHERE id = %s",
                        (logo_path, empresa_id),
                    )
            conn.commit()
        finally:
            conn.close()
        if logo_file and logo_file.filename and not tem_logo_path:
            flash("Empresa atualizada, mas a coluna do logo ainda não existe no banco. Rode o SQL manual para habilitar o logo.", "warning")
            return redirect(url_for("empresas_editar", empresa_id=empresa_id))
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
        return {"ok": False, "message": "Produto nÃ£o encontrado."}, 404

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


