import os
import hashlib
import json
import secrets
from io import BytesIO
from datetime import date, datetime, timedelta
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
    send_file,
)
import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except Exception:
    colors = None
    TA_LEFT = TA_RIGHT = None
    A4 = None
    ParagraphStyle = getSampleStyleSheet = None
    mm = None
    Image = None
    Paragraph = SimpleDocTemplate = Spacer = Table = TableStyle = None


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
app.config["SESSION_PERMANENT"] = False
schema_checked = False
SERVER_INSTANCE_TOKEN = secrets.token_hex(16)
SCREEN_GROUPS = [
    {
        "title": "Operacao",
        "items": [
            {"key": "dashboard", "label": "Dashboard"},
            {"key": "pessoas", "label": "Clientes"},
            {"key": "produtos", "label": "Produtos"},
            {"key": "familias_produtos", "label": "Familias de produtos"},
            {"key": "condicoes_pagamento", "label": "Condicoes de pagamento"},
            {"key": "vendas", "label": "Vendas"},
        ],
    },
    {
        "title": "Compras e financeiro",
        "items": [
            {"key": "entradas_notas", "label": "Entradas de nota"},
            {"key": "contas_pagar", "label": "Contas a pagar"},
            {"key": "caixa", "label": "Caixa"},
            {"key": "contas_receber", "label": "Contas a receber"},
            {"key": "balancete", "label": "Balancete"},
        ],
    },
    {
        "title": "Gestao",
        "items": [
            {"key": "relatorios", "label": "Relatorios"},
            {"key": "empresas", "label": "Empresas"},
        ],
    },
]
SCREEN_KEYS = [item["key"] for group in SCREEN_GROUPS for item in group["items"]]
SCREEN_ENDPOINTS = {
    "dashboard": "dashboard",
    "pessoas": "pessoas",
    "produtos": "produtos",
    "familias_produtos": "familias_produtos",
    "condicoes_pagamento": "condicoes_pagamento",
    "vendas": "vendas",
    "entradas_notas": "entradas_notas",
    "contas_pagar": "contas_pagar",
    "caixa": "caixa",
    "contas_receber": "contas_receber",
    "balancete": "balancete",
    "relatorios": "relatorio_estoque_minimo",
    "empresas": "empresas",
}


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


def parse_date_value(value, default=None):
    text = (value or "").strip()
    if not text:
        return default
    try:
        return date.fromisoformat(text)
    except ValueError:
        return default


def split_amount(total, parcelas):
    parcelas = max(int(parcelas or 1), 1)
    total_centavos = int(round(float(total or 0) * 100))
    base = total_centavos // parcelas
    restante = total_centavos - (base * parcelas)
    valores = []
    for indice in range(parcelas):
        extra = 1 if indice < restante else 0
        valores.append((base + extra) / 100.0)
    return valores


def get_pessoas_ativas():
    return fetch_all(
        """
        SELECT id, tipo, tipo_cadastro, nome, documento
        FROM public.pessoas
        WHERE ativo = TRUE
        ORDER BY nome
        """
    )


def get_pessoas_por_cadastro(tipo_cadastro=None, somente_ativas=True):
    where = []
    params = []
    if somente_ativas:
        where.append("ativo = TRUE")
    if tipo_cadastro == "CLIENTE":
        where.append("COALESCE(tipo_cadastro, 'CLIENTE') IN ('CLIENTE', 'AMBOS')")
    elif tipo_cadastro == "FORNECEDOR":
        where.append("COALESCE(tipo_cadastro, 'CLIENTE') IN ('FORNECEDOR', 'AMBOS')")

    query = """
        SELECT id, tipo, COALESCE(tipo_cadastro, 'CLIENTE') AS tipo_cadastro, nome, documento
        FROM public.pessoas
    """
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY nome"
    return fetch_all(query, tuple(params))


def get_condicoes_pagamento_ativas():
    return fetch_all(
        """
        SELECT
            id, nome, forma_pagamento, parcelas,
            dias_intervalo, taxa_percentual,
            COALESCE(finalidade, 'AMBOS') AS finalidade
        FROM public.condicoes_pagamento
        WHERE ativo = TRUE
        ORDER BY nome
        """
    )


def get_condicoes_pagamento_por_finalidade(finalidade=None, somente_ativas=True):
    where = []
    params = []
    if somente_ativas:
        where.append("ativo = TRUE")
    if finalidade in ("VENDA", "COMPRA"):
        where.append(f"COALESCE(finalidade, 'AMBOS') IN ('{finalidade}', 'AMBOS')")

    query = """
        SELECT
            id, nome, forma_pagamento, parcelas,
            dias_intervalo, taxa_percentual,
            COALESCE(finalidade, 'AMBOS') AS finalidade
        FROM public.condicoes_pagamento
    """
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY nome"
    return fetch_all(query, tuple(params))


def parse_screen_permissions(raw_value):
    if not raw_value:
        return None
    try:
        data = json.loads(raw_value)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return [key for key in data if key in SCREEN_KEYS]


def get_selected_screen_permissions(form):
    selected = []
    for key in SCREEN_KEYS:
        if f"screen_{key}" in form:
            selected.append(key)
    return selected


def has_screen_access(screen_key):
    if session.get("perfil") == "admin":
        return True
    allowed = parse_screen_permissions(session.get("telas_permitidas"))
    if allowed is None:
        return True
    return screen_key in allowed


def get_first_allowed_endpoint():
    for key in SCREEN_KEYS:
        if has_screen_access(key):
            return SCREEN_ENDPOINTS.get(key, "dashboard")
    return "logout"


def get_user_operational_permissions(user_id):
    return fetch_one(
        """
        SELECT
            COALESCE(permite_editar_venda, FALSE) AS permite_editar_venda,
            COALESCE(permite_estornar_venda, FALSE) AS permite_estornar_venda,
            COALESCE(permite_excluir_venda, FALSE) AS permite_excluir_venda,
            COALESCE(permite_inventario, FALSE) AS permite_inventario,
            COALESCE(permite_editar_compra, FALSE) AS permite_editar_compra,
            COALESCE(permite_excluir_compra, FALSE) AS permite_excluir_compra,
            COALESCE(permite_estornar_compra, FALSE) AS permite_estornar_compra,
            COALESCE(permite_baixar_contas_pagar, FALSE) AS permite_baixar_contas_pagar,
            COALESCE(permite_editar_financeiro, FALSE) AS permite_editar_financeiro,
            COALESCE(permite_abrir_caixa, FALSE) AS permite_abrir_caixa,
            COALESCE(permite_fechar_caixa, FALSE) AS permite_fechar_caixa,
            COALESCE(permite_suprimento_caixa, FALSE) AS permite_suprimento_caixa,
            COALESCE(permite_sangria_caixa, FALSE) AS permite_sangria_caixa,
            COALESCE(permite_receber_venda_caixa, FALSE) AS permite_receber_venda_caixa,
            COALESCE(permite_baixar_contas_receber, FALSE) AS permite_baixar_contas_receber,
            COALESCE(permite_ver_balancete, FALSE) AS permite_ver_balancete
        FROM public.usuarios
        WHERE id = %s
        """,
        (user_id,),
    ) or {}


def log_financeiro(acao, descricao, conta_pagar_id=None, compra_id=None, usuario_id=None, cur=None):
    usuario_id = usuario_id if usuario_id is not None else session.get("user_id")
    sql = """
        INSERT INTO public.financeiro_logs (
            conta_pagar_id,
            compra_id,
            usuario_id,
            acao,
            descricao
        )
        VALUES (%s, %s, %s, %s, %s)
    """
    params = (conta_pagar_id, compra_id, usuario_id, acao, descricao)
    if cur is not None:
        cur.execute(sql, params)
    else:
        execute(sql, params)


def replace_compra_financeiro(cur, compra_id, fornecedor_id, numero_nota, faturas, status_compra):
    cur.execute("DELETE FROM public.compras_pagamentos WHERE compra_id = %s", (compra_id,))
    cur.execute("DELETE FROM public.contas_pagar WHERE compra_id = %s", (compra_id,))

    status_fatura = "PENDENTE" if status_compra == "FINALIZADA" else "RASCUNHO"
    for fatura in faturas:
        cur.execute(
            """
            INSERT INTO public.compras_pagamentos (
                compra_id,
                condicao_pagamento_id,
                descricao_condicao,
                forma_pagamento,
                valor,
                parcelas,
                dias_intervalo,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                compra_id,
                fatura["condicao_pagamento_id"],
                fatura["descricao_condicao"],
                fatura["forma_pagamento"],
                fatura["valor"],
                1,
                0,
                status_fatura,
            ),
        )
        pagamento_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO public.contas_pagar (
                compra_id,
                pagamento_id,
                fornecedor_id,
                numero_parcela,
                descricao,
                valor,
                data_vencimento,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                compra_id,
                pagamento_id,
                fornecedor_id,
                fatura["numero_parcela"],
                fatura["descricao"] or f"Nota {numero_nota} - parcela {fatura['numero_parcela']}",
                fatura["valor"],
                fatura["data_vencimento"],
                status_fatura,
            ),
        )


def aplicar_finalizacao_compra(cur, compra_id, numero_nota):
    cur.execute(
        """
        SELECT
            ci.produto_id,
            ci.quantidade,
            ci.valor_unitario,
            COALESCE(p.estoque_atual, 0) AS estoque_atual
        FROM public.compra_itens ci
        INNER JOIN public.produtos p ON p.id = ci.produto_id
        WHERE ci.compra_id = %s
        """,
        (compra_id,),
    )
    itens = cur.fetchall()
    for item in itens:
        estoque_anterior = float(item["estoque_atual"] or 0)
        quantidade = float(item["quantidade"] or 0)
        cur.execute(
            """
            UPDATE public.produtos
               SET estoque_atual = COALESCE(estoque_atual, 0) + %s,
                   custo = %s
             WHERE id = %s
            """,
            (quantidade, item["valor_unitario"], item["produto_id"]),
        )
        registrar_movimentacao_estoque(
            produto_id=item["produto_id"],
            tipo_movimento="ENTRADA_NOTA",
            quantidade=quantidade,
            estoque_anterior=estoque_anterior,
            estoque_posterior=estoque_anterior + quantidade,
            origem="COMPRA",
            referencia_id=compra_id,
            observacao=f"Entrada pela nota #{numero_nota}.",
            cur=cur,
        )

    cur.execute(
        """
        UPDATE public.contas_pagar
           SET status = CASE WHEN COALESCE(status, 'RASCUNHO') = 'PAGO' THEN 'PAGO' ELSE 'PENDENTE' END
         WHERE compra_id = %s
           AND COALESCE(status, 'RASCUNHO') <> 'ESTORNADO'
        """,
        (compra_id,),
    )
    cur.execute(
        """
        UPDATE public.compras_pagamentos
           SET status = CASE WHEN COALESCE(status, 'RASCUNHO') = 'PAGO' THEN 'PAGO' ELSE 'PENDENTE' END
         WHERE compra_id = %s
           AND COALESCE(status, 'RASCUNHO') <> 'ESTORNADO'
        """,
        (compra_id,),
    )


def reverter_finalizacao_compra(cur, compra_id, numero_nota, motivo_movimento):
    cur.execute(
        """
        SELECT
            ci.produto_id,
            ci.quantidade,
            COALESCE(p.estoque_atual, 0) AS estoque_atual
        FROM public.compra_itens ci
        INNER JOIN public.produtos p ON p.id = ci.produto_id
        WHERE ci.compra_id = %s
        """,
        (compra_id,),
    )
    itens = cur.fetchall()
    for item in itens:
        estoque_anterior = float(item["estoque_atual"] or 0)
        quantidade = float(item["quantidade"] or 0)
        cur.execute(
            """
            UPDATE public.produtos
               SET estoque_atual = COALESCE(estoque_atual, 0) - %s
             WHERE id = %s
            """,
            (quantidade, item["produto_id"]),
        )
        registrar_movimentacao_estoque(
            produto_id=item["produto_id"],
            tipo_movimento=motivo_movimento,
            quantidade=-quantidade,
            estoque_anterior=estoque_anterior,
            estoque_posterior=estoque_anterior - quantidade,
            origem="COMPRA",
            referencia_id=compra_id,
            observacao=f"Reversao da nota #{numero_nota}.",
            cur=cur,
        )


def get_compra_bloqueios_movimentacao(compra_id):
    return fetch_all(
        """
        WITH entrada AS (
            SELECT
                m.produto_id,
                MAX(m.created_at) AS data_entrada
            FROM public.movimentacoes_estoque m
            WHERE m.origem = 'COMPRA'
              AND m.referencia_id = %s
              AND m.tipo_movimento = 'ENTRADA_NOTA'
            GROUP BY m.produto_id
        )
        SELECT
            p.descricao AS produto,
            m.tipo_movimento,
            m.created_at
        FROM entrada e
        INNER JOIN public.movimentacoes_estoque m
            ON m.produto_id = e.produto_id
           AND m.created_at > e.data_entrada
        INNER JOIN public.produtos p
            ON p.id = e.produto_id
        WHERE NOT (m.origem = 'COMPRA' AND m.referencia_id = %s AND m.tipo_movimento = 'ENTRADA_NOTA')
        ORDER BY m.created_at DESC
        """,
        (compra_id, compra_id),
    )


def compra_pode_alterar_estoque(compra_id, status_compra):
    status_compra = (status_compra or "ABERTA").upper()
    if status_compra not in ("FINALIZADA", "ATIVA"):
        return True, []
    bloqueios = get_compra_bloqueios_movimentacao(compra_id)
    return len(bloqueios) == 0, bloqueios


def parse_faturas_compra(form, data_base, condicoes_compra):
    fatura_numeros = form.getlist("fatura_numero[]")
    fatura_descricoes = form.getlist("fatura_descricao[]")
    fatura_valores = form.getlist("fatura_valor[]")
    fatura_vencimentos = form.getlist("fatura_vencimento[]")
    condicao_pagamento_id = form.get("condicao_pagamento_id") or None

    condicao = None
    if condicao_pagamento_id:
        try:
            condicao = next((c for c in condicoes_compra if c["id"] == int(condicao_pagamento_id)), None)
        except ValueError:
            condicao = None

    faturas = []
    for i in range(max(len(fatura_numeros), len(fatura_valores), len(fatura_vencimentos))):
        valor = parse_decimal(fatura_valores[i] if i < len(fatura_valores) else "0")
        vencimento = parse_date_value(fatura_vencimentos[i] if i < len(fatura_vencimentos) else "", data_base)
        descricao = (fatura_descricoes[i] if i < len(fatura_descricoes) else "").strip()
        numero_parcela = int(fatura_numeros[i] or (i + 1)) if i < len(fatura_numeros) and str(fatura_numeros[i]).strip() else (i + 1)
        if valor <= 0:
            continue
        faturas.append(
            {
                "numero_parcela": numero_parcela,
                "descricao": descricao,
                "valor": float(valor),
                "data_vencimento": vencimento,
                "condicao_pagamento_id": condicao["id"] if condicao else None,
                "descricao_condicao": condicao["nome"] if condicao else "",
                "forma_pagamento": condicao["forma_pagamento"] if condicao else "",
            }
        )
    return condicao, faturas


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


def slugify_filename_part(value):
    texto = (value or "").strip().lower()
    mapa = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüçñ ",
        "aaaaaeeeeiiiiooooouuuucn-",
    )
    texto = texto.translate(mapa)
    permitido = []
    for char in texto:
        if char.isalnum() or char in ("-", "_"):
            permitido.append(char)
    resultado = "".join(permitido).strip("-_")
    return resultado or "usuario"


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
            ADD COLUMN IF NOT EXISTS permite_editar_compra BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_excluir_compra BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_estornar_compra BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_baixar_contas_pagar BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_editar_financeiro BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_abrir_caixa BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_fechar_caixa BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_suprimento_caixa BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_sangria_caixa BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_receber_venda_caixa BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_baixar_contas_receber BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS permite_ver_balancete BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS session_token TEXT
        """,
        """
        CREATE TABLE IF NOT EXISTS public.pessoas (
            id BIGSERIAL PRIMARY KEY,
            tipo VARCHAR(20) DEFAULT 'FISICA',
            tipo_cadastro VARCHAR(20) DEFAULT 'CLIENTE',
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
            ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) DEFAULT 'FISICA',
            ADD COLUMN IF NOT EXISTS tipo_cadastro VARCHAR(20) DEFAULT 'CLIENTE',
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
            ADD COLUMN IF NOT EXISTS modo_operacao VARCHAR(20) DEFAULT 'PDV',
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
            ADD COLUMN IF NOT EXISTS finalidade VARCHAR(20) DEFAULT 'AMBOS',
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
        CREATE TABLE IF NOT EXISTS public.compras (
            id BIGSERIAL PRIMARY KEY,
            fornecedor_id BIGINT REFERENCES public.pessoas(id),
            usuario_id BIGINT REFERENCES public.usuarios(id),
            numero_nota VARCHAR(40) NOT NULL,
            serie VARCHAR(20),
            data_emissao DATE NOT NULL,
            data_entrada TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            valor_produtos NUMERIC(14,2) DEFAULT 0,
            desconto NUMERIC(14,2) DEFAULT 0,
            acrescimo NUMERIC(14,2) DEFAULT 0,
            valor_total NUMERIC(14,2) DEFAULT 0,
            forma_pagamento VARCHAR(30),
            parcelas INTEGER DEFAULT 1,
            valor_parcela NUMERIC(14,2) DEFAULT 0,
            condicao_pagamento_id BIGINT REFERENCES public.condicoes_pagamento(id),
            observacoes TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'ABERTA',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE public.compras
            ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'ABERTA'
        """,
        """
        CREATE TABLE IF NOT EXISTS public.compra_itens (
            id BIGSERIAL PRIMARY KEY,
            compra_id BIGINT NOT NULL REFERENCES public.compras(id) ON DELETE CASCADE,
            produto_id BIGINT NOT NULL REFERENCES public.produtos(id),
            quantidade NUMERIC(14,3) NOT NULL DEFAULT 0,
            valor_unitario NUMERIC(14,2) NOT NULL DEFAULT 0,
            total NUMERIC(14,2) NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS public.compras_pagamentos (
            id BIGSERIAL PRIMARY KEY,
            compra_id BIGINT NOT NULL REFERENCES public.compras(id) ON DELETE CASCADE,
            condicao_pagamento_id BIGINT REFERENCES public.condicoes_pagamento(id),
            descricao_condicao TEXT,
            forma_pagamento VARCHAR(30),
            valor NUMERIC(14,2) NOT NULL DEFAULT 0,
            parcelas INTEGER NOT NULL DEFAULT 1,
            dias_intervalo INTEGER NOT NULL DEFAULT 30,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDENTE',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS public.contas_pagar (
            id BIGSERIAL PRIMARY KEY,
            compra_id BIGINT REFERENCES public.compras(id) ON DELETE CASCADE,
            pagamento_id BIGINT REFERENCES public.compras_pagamentos(id) ON DELETE CASCADE,
            fornecedor_id BIGINT REFERENCES public.pessoas(id),
            numero_parcela INTEGER NOT NULL DEFAULT 1,
            descricao TEXT,
            valor NUMERIC(14,2) NOT NULL DEFAULT 0,
            data_vencimento DATE NOT NULL,
            data_pagamento DATE,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDENTE',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS public.financeiro_logs (
            id BIGSERIAL PRIMARY KEY,
            conta_pagar_id BIGINT REFERENCES public.contas_pagar(id) ON DELETE SET NULL,
            compra_id BIGINT REFERENCES public.compras(id) ON DELETE SET NULL,
            usuario_id BIGINT REFERENCES public.usuarios(id),
            acao VARCHAR(40) NOT NULL,
            descricao TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
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
    ensure_column("usuarios", "telas_permitidas", "TEXT")
    ensure_column("usuarios", "permite_estornar_compra", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_baixar_contas_pagar", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_editar_financeiro", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_editar_compra", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_excluir_compra", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_abrir_caixa", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_fechar_caixa", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_suprimento_caixa", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_sangria_caixa", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_receber_venda_caixa", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_baixar_contas_receber", "BOOLEAN DEFAULT FALSE")
    ensure_column("usuarios", "permite_ver_balancete", "BOOLEAN DEFAULT FALSE")
    ensure_column("pessoas", "endereco", "TEXT")
    ensure_column("pessoas", "cep", "VARCHAR(20)")
    ensure_column("pessoas", "observacoes", "TEXT")
    ensure_column("pessoas", "tipo_cadastro", "VARCHAR(20) DEFAULT 'CLIENTE'")
    ensure_column("condicoes_pagamento", "finalidade", "VARCHAR(20) DEFAULT 'AMBOS'")
    ensure_column("empresas", "modo_operacao", "VARCHAR(20) DEFAULT 'PDV'")
    ensure_column("compras", "status", "VARCHAR(20) DEFAULT 'ABERTA'")
    ensure_column("compras_pagamentos", "status", "VARCHAR(20) DEFAULT 'PENDENTE'")
    ensure_column("contas_pagar", "pagamento_id", "BIGINT")
    ensure_column("financeiro_logs", "descricao", "TEXT")

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


def screen_required(screen_key):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if not has_screen_access(screen_key):
                flash("Você não tem permissão para acessar esta tela.", "danger")
                return redirect(url_for(get_first_allowed_endpoint()))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# =========================================================
# CONTEXT
# =========================================================
@app.context_processor
def inject_user():
    branding = get_empresa_branding()
    allowed = parse_screen_permissions(session.get("telas_permitidas"))
    screen_access = {key: True for key in SCREEN_KEYS} if session.get("perfil") == "admin" else {
        key: True if allowed is None else key in allowed for key in SCREEN_KEYS
    }
    return {
        "session_user_nome": session.get("nome"),
        "session_user_perfil": session.get("perfil"),
        "screen_access": screen_access,
        "screen_groups": SCREEN_GROUPS,
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
    server_instance_token = session.get("server_instance_token")
    if not user_id or not session_token:
        return
    if server_instance_token != SERVER_INSTANCE_TOKEN:
        session.clear()
        return redirect(url_for("login"))

    user = fetch_one(
        """
        SELECT COALESCE(permite_login_multiplo, TRUE) AS permite_login_multiplo,
               session_token,
               ativo,
               telas_permitidas
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

    session["telas_permitidas"] = user.get("telas_permitidas")


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
                   COALESCE(permite_login_multiplo, TRUE) AS permite_login_multiplo,
                   telas_permitidas
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
        session["telas_permitidas"] = user.get("telas_permitidas")
        session["server_instance_token"] = SERVER_INSTANCE_TOKEN
        session.permanent = False

        return redirect(url_for(get_first_allowed_endpoint()))

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
@screen_required("dashboard")
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

    clientes = get_pessoas_por_cadastro("CLIENTE")

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
    termo = request.args.get("q", "").strip()
    where = []
    params = []
    if termo:
        where.append("(nome ILIKE %s OR username ILIKE %s)")
        params.extend((f"%{termo}%", f"%{termo}%"))

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
               COALESCE(permite_editar_compra, FALSE) AS permite_editar_compra,
               COALESCE(permite_excluir_compra, FALSE) AS permite_excluir_compra,
               COALESCE(permite_estornar_compra, FALSE) AS permite_estornar_compra,
               COALESCE(permite_baixar_contas_pagar, FALSE) AS permite_baixar_contas_pagar,
               COALESCE(permite_editar_financeiro, FALSE) AS permite_editar_financeiro,
               COALESCE(permite_abrir_caixa, FALSE) AS permite_abrir_caixa,
               COALESCE(permite_fechar_caixa, FALSE) AS permite_fechar_caixa,
               COALESCE(permite_suprimento_caixa, FALSE) AS permite_suprimento_caixa,
               COALESCE(permite_sangria_caixa, FALSE) AS permite_sangria_caixa,
               COALESCE(permite_receber_venda_caixa, FALSE) AS permite_receber_venda_caixa,
               COALESCE(permite_baixar_contas_receber, FALSE) AS permite_baixar_contas_receber,
               COALESCE(permite_ver_balancete, FALSE) AS permite_ver_balancete,
               telas_permitidas,
               ativo, created_at
        FROM public.usuarios
        """
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY id DESC",
        tuple(params),
    )
    for row in rows:
        row["telas_permitidas_lista"] = parse_screen_permissions(row.get("telas_permitidas")) or SCREEN_KEYS
    return render_template("usuarios.html", usuarios=rows, q=termo)


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
    permite_editar_compra = "permite_editar_compra" in request.form
    permite_excluir_compra = "permite_excluir_compra" in request.form
    permite_estornar_compra = "permite_estornar_compra" in request.form
    permite_baixar_contas_pagar = "permite_baixar_contas_pagar" in request.form
    permite_editar_financeiro = "permite_editar_financeiro" in request.form
    permite_abrir_caixa = "permite_abrir_caixa" in request.form
    permite_fechar_caixa = "permite_fechar_caixa" in request.form
    permite_suprimento_caixa = "permite_suprimento_caixa" in request.form
    permite_sangria_caixa = "permite_sangria_caixa" in request.form
    permite_receber_venda_caixa = "permite_receber_venda_caixa" in request.form
    permite_baixar_contas_receber = "permite_baixar_contas_receber" in request.form
    permite_ver_balancete = "permite_ver_balancete" in request.form
    telas_selecionadas = get_selected_screen_permissions(request.form)
    telas_permitidas = json.dumps(telas_selecionadas)
    ativo = "ativo" in request.form

    if not nome or not username or not senha:
        flash("Preencha nome, usuÃ¡rio e senha.", "warning")
        return redirect(url_for("usuarios"))
    if not telas_selecionadas:
        flash("Selecione pelo menos uma tela visível para o usuário.", "warning")
        return redirect(url_for("usuarios"))

    try:
        execute(
            """
            INSERT INTO public.usuarios
            (
                nome, username, password_hash, perfil,
                pode_vender_abaixo_custo, desconto_maximo_percentual,
                permite_login_multiplo, permite_editar_venda, permite_estornar_venda,
                permite_excluir_venda, permite_inventario, permite_editar_compra, permite_excluir_compra,
                permite_estornar_compra, permite_baixar_contas_pagar, permite_editar_financeiro,
                permite_abrir_caixa, permite_fechar_caixa, permite_suprimento_caixa, permite_sangria_caixa,
                permite_receber_venda_caixa, permite_baixar_contas_receber, permite_ver_balancete,
                telas_permitidas,
                ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                nome, username, hash_password(senha), perfil,
                pode_vender_abaixo_custo, desconto_maximo_percentual,
                permite_login_multiplo, permite_editar_venda, permite_estornar_venda,
                permite_excluir_venda, permite_inventario, permite_editar_compra, permite_excluir_compra,
                permite_estornar_compra, permite_baixar_contas_pagar, permite_editar_financeiro,
                permite_abrir_caixa, permite_fechar_caixa, permite_suprimento_caixa, permite_sangria_caixa,
                permite_receber_venda_caixa, permite_baixar_contas_receber, permite_ver_balancete,
                telas_permitidas,
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
               COALESCE(permite_editar_compra, FALSE) AS permite_editar_compra,
               COALESCE(permite_excluir_compra, FALSE) AS permite_excluir_compra,
               COALESCE(permite_estornar_compra, FALSE) AS permite_estornar_compra,
               COALESCE(permite_baixar_contas_pagar, FALSE) AS permite_baixar_contas_pagar,
               COALESCE(permite_editar_financeiro, FALSE) AS permite_editar_financeiro,
               COALESCE(permite_abrir_caixa, FALSE) AS permite_abrir_caixa,
               COALESCE(permite_fechar_caixa, FALSE) AS permite_fechar_caixa,
               COALESCE(permite_suprimento_caixa, FALSE) AS permite_suprimento_caixa,
               COALESCE(permite_sangria_caixa, FALSE) AS permite_sangria_caixa,
               COALESCE(permite_receber_venda_caixa, FALSE) AS permite_receber_venda_caixa,
               COALESCE(permite_baixar_contas_receber, FALSE) AS permite_baixar_contas_receber,
               COALESCE(permite_ver_balancete, FALSE) AS permite_ver_balancete,
               telas_permitidas,
               ativo
        FROM public.usuarios
        WHERE id = %s
        """,
        (usuario_id,),
    )

    if not usuario:
        flash("Usuário não encontrado.", "warning")
        return redirect(url_for("usuarios"))

    usuario["telas_permitidas_lista"] = parse_screen_permissions(usuario.get("telas_permitidas")) or SCREEN_KEYS

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
        permite_editar_compra = "permite_editar_compra" in request.form
        permite_excluir_compra = "permite_excluir_compra" in request.form
        permite_estornar_compra = "permite_estornar_compra" in request.form
        permite_baixar_contas_pagar = "permite_baixar_contas_pagar" in request.form
        permite_editar_financeiro = "permite_editar_financeiro" in request.form
        permite_abrir_caixa = "permite_abrir_caixa" in request.form
        permite_fechar_caixa = "permite_fechar_caixa" in request.form
        permite_suprimento_caixa = "permite_suprimento_caixa" in request.form
        permite_sangria_caixa = "permite_sangria_caixa" in request.form
        permite_receber_venda_caixa = "permite_receber_venda_caixa" in request.form
        permite_baixar_contas_receber = "permite_baixar_contas_receber" in request.form
        permite_ver_balancete = "permite_ver_balancete" in request.form
        telas_selecionadas = get_selected_screen_permissions(request.form)
        telas_permitidas = json.dumps(telas_selecionadas)
        ativo = "ativo" in request.form

        if not nome or not username:
            flash("Preencha nome e usuário.", "warning")
            usuario["telas_permitidas_lista"] = telas_selecionadas
            return render_template("usuario_editar.html", usuario=usuario)
        if not telas_selecionadas:
            flash("Selecione pelo menos uma tela visível para o usuário.", "warning")
            usuario["telas_permitidas_lista"] = []
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
                           permite_editar_compra = %s,
                           permite_excluir_compra = %s,
                           permite_estornar_compra = %s,
                           permite_baixar_contas_pagar = %s,
                           permite_editar_financeiro = %s,
                           permite_abrir_caixa = %s,
                           permite_fechar_caixa = %s,
                           permite_suprimento_caixa = %s,
                           permite_sangria_caixa = %s,
                           permite_receber_venda_caixa = %s,
                           permite_baixar_contas_receber = %s,
                           permite_ver_balancete = %s,
                           telas_permitidas = %s,
                           ativo = %s
                     WHERE id = %s
                    """,
                    (
                        nome, username, hash_password(senha), perfil,
                        pode_vender_abaixo_custo, desconto_maximo_percentual,
                        permite_login_multiplo, permite_editar_venda, permite_estornar_venda,
                        permite_excluir_venda, permite_inventario, permite_editar_compra, permite_excluir_compra,
                        permite_estornar_compra, permite_baixar_contas_pagar, permite_editar_financeiro,
                        permite_abrir_caixa, permite_fechar_caixa, permite_suprimento_caixa, permite_sangria_caixa,
                        permite_receber_venda_caixa, permite_baixar_contas_receber, permite_ver_balancete,
                        telas_permitidas,
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
                           permite_editar_compra = %s,
                           permite_excluir_compra = %s,
                           permite_estornar_compra = %s,
                           permite_baixar_contas_pagar = %s,
                           permite_editar_financeiro = %s,
                           permite_abrir_caixa = %s,
                           permite_fechar_caixa = %s,
                           permite_suprimento_caixa = %s,
                           permite_sangria_caixa = %s,
                           permite_receber_venda_caixa = %s,
                           permite_baixar_contas_receber = %s,
                           permite_ver_balancete = %s,
                           telas_permitidas = %s,
                           ativo = %s
                     WHERE id = %s
                    """,
                    (
                        nome, username, perfil,
                        pode_vender_abaixo_custo, desconto_maximo_percentual,
                        permite_login_multiplo, permite_editar_venda, permite_estornar_venda,
                        permite_excluir_venda, permite_inventario, permite_editar_compra, permite_excluir_compra,
                        permite_estornar_compra, permite_baixar_contas_pagar, permite_editar_financeiro,
                        permite_abrir_caixa, permite_fechar_caixa, permite_suprimento_caixa, permite_sangria_caixa,
                        permite_receber_venda_caixa, permite_baixar_contas_receber, permite_ver_balancete,
                        telas_permitidas,
                        ativo, usuario_id
                    ),
                )

            if session.get("user_id") == usuario_id:
                session["telas_permitidas"] = telas_permitidas
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
                    "permite_editar_compra": permite_editar_compra,
                    "permite_excluir_compra": permite_excluir_compra,
                    "permite_estornar_compra": permite_estornar_compra,
                    "permite_baixar_contas_pagar": permite_baixar_contas_pagar,
                    "permite_editar_financeiro": permite_editar_financeiro,
                    "permite_abrir_caixa": permite_abrir_caixa,
                    "permite_fechar_caixa": permite_fechar_caixa,
                    "permite_suprimento_caixa": permite_suprimento_caixa,
                    "permite_sangria_caixa": permite_sangria_caixa,
                    "permite_receber_venda_caixa": permite_receber_venda_caixa,
                    "permite_baixar_contas_receber": permite_baixar_contas_receber,
                    "permite_ver_balancete": permite_ver_balancete,
                    "telas_permitidas": telas_permitidas,
                    "telas_permitidas_lista": parse_screen_permissions(telas_permitidas) or [],
                    "ativo": ativo,
                }
            )

    return render_template("usuario_editar.html", usuario=usuario)


# =========================================================
# PESSOAS / CLIENTES
# =========================================================
@app.route("/pessoas")
@login_required
@screen_required("pessoas")
def pessoas():
    termo = request.args.get("q", "").strip()
    tipo_cadastro = (request.args.get("tipo_cadastro") or "").strip().upper()
    config = get_empresa_configuracoes()
    where = []
    params = []

    if tipo_cadastro in ("CLIENTE", "FORNECEDOR", "AMBOS"):
        where.append("COALESCE(tipo_cadastro, 'CLIENTE') = %s")
        params.append(tipo_cadastro)

    if termo:
        where.append("(nome ILIKE %s OR documento ILIKE %s OR telefone ILIKE %s OR email ILIKE %s)")
        params.extend((f"%{termo}%", f"%{termo}%", f"%{termo}%", f"%{termo}%"))

    query = """
        SELECT id, tipo, COALESCE(tipo_cadastro, 'CLIENTE') AS tipo_cadastro,
               nome, documento, telefone, email, cidade, estado, ativo, created_at
        FROM public.pessoas
    """
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY id DESC"
    rows = fetch_all(query, tuple(params))

    return render_template("pessoas.html", pessoas=rows, termo=termo, tipo_cadastro=tipo_cadastro, config=config)


@app.route("/pessoas/novo", methods=["POST"])
@login_required
@screen_required("pessoas")
def pessoas_novo():
    config = get_empresa_configuracoes()
    tipo = request.form.get("tipo", "").strip()
    tipo_cadastro = (request.form.get("tipo_cadastro") or "").strip().upper() or "CLIENTE"
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
    if tipo_cadastro not in ("CLIENTE", "FORNECEDOR", "AMBOS"):
        flash("Selecione um tipo de cadastro válido.", "warning")
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
                tipo, tipo_cadastro, nome, documento, telefone, email,
                endereco, cidade, estado, cep, observacoes, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tipo, tipo_cadastro, nome, documento, telefone, email,
                endereco, cidade, estado, cep, observacoes, ativo
            ),
        )
        flash("Cadastro salvo com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar cliente: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("pessoas"))


@app.route("/pessoas/<int:pessoa_id>/editar", methods=["GET", "POST"])
@login_required
@screen_required("pessoas")
def pessoas_editar(pessoa_id):
    config = get_empresa_configuracoes()
    pessoa = fetch_one(
        """
        SELECT
            id, tipo, COALESCE(tipo_cadastro, 'CLIENTE') AS tipo_cadastro, nome, documento, telefone, email,
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
        tipo_cadastro = (request.form.get("tipo_cadastro") or "").strip().upper() or "CLIENTE"
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
        if tipo_cadastro not in ("CLIENTE", "FORNECEDOR", "AMBOS"):
            flash("Selecione um tipo de cadastro válido.", "warning")
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
                       tipo_cadastro = %s,
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
                    tipo_cadastro,
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
                    "tipo_cadastro": tipo_cadastro,
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
@screen_required("produtos")
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
@screen_required("relatorios")
def relatorio_estoque_minimo():
    termo = request.args.get("q", "").strip()
    contexto = montar_relatorio_estoque_minimo_contexto(termo)
    return render_template("relatorio_estoque_minimo.html", **contexto)


@app.route("/relatorios/estoque-minimo/imprimir")
@login_required
@screen_required("relatorios")
def relatorio_estoque_minimo_imprimir():
    if not SimpleDocTemplate:
        flash("A geração de PDF não está disponível porque a dependência reportlab não foi instalada.", "warning")
        return redirect(url_for("relatorio_estoque_minimo"))

    termo = request.args.get("q", "").strip()
    contexto = montar_relatorio_estoque_minimo_contexto(termo)
    data_impressao = datetime.now()
    usuario_nome = slugify_filename_part(session.get("nome") or "usuario")
    contexto["data_impressao"] = data_impressao
    contexto["pdf_filename"] = f"{data_impressao.strftime('%d-%m-%Y-%H%M')}-estoque-minimo-{usuario_nome}.pdf"
    pdf_bytes = gerar_pdf_relatorio_estoque_minimo(contexto)
    return send_file(
        pdf_bytes,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=contexto["pdf_filename"],
    )


def montar_relatorio_estoque_minimo_contexto(termo=""):
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

    return {
        "produtos": produtos,
        "termo": termo,
        "total_produtos": total_produtos,
        "abaixo_minimo": abaixo_minimo,
        "no_minimo": no_minimo,
        "acima_minimo": acima_minimo,
        "total_sugerido": total_sugerido,
    }


def gerar_pdf_relatorio_estoque_minimo(contexto):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=contexto.get("pdf_filename") or "relatorio-estoque-minimo.pdf",
    )

    styles = getSampleStyleSheet()
    style_normal = ParagraphStyle(
        "RbNormal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#52606d"),
    )
    style_title = ParagraphStyle(
        "RbTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=22,
        textColor=colors.HexColor("#102a43"),
        spaceAfter=3,
    )
    style_brand = ParagraphStyle(
        "RbBrand",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        textColor=colors.HexColor("#102a43"),
        spaceAfter=4,
    )
    style_small_label = ParagraphStyle(
        "RbSmallLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#7b8794"),
    )
    style_small_value = ParagraphStyle(
        "RbSmallValue",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#334e68"),
    )
    style_meta_label = ParagraphStyle(
        "RbMetaLabel",
        parent=style_small_label,
        fontSize=7.5,
        leading=9,
        spaceAfter=1,
    )
    style_meta_value = ParagraphStyle(
        "RbMetaValue",
        parent=style_small_value,
        fontSize=9.5,
        leading=11,
        spaceAfter=5,
    )
    style_metric_label = ParagraphStyle(
        "RbMetricLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#7b8794"),
    )
    style_metric_value = ParagraphStyle(
        "RbMetricValue",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=18,
        textColor=colors.HexColor("#102a43"),
    )
    style_right = ParagraphStyle(
        "RbRight",
        parent=style_small_value,
        alignment=TA_RIGHT,
    )

    story = []
    logo_cell = ""
    empresa = get_empresa_configuracoes()
    logo_path = (empresa.get("logo_path") or "").strip()
    if logo_path:
        logo_file = LOGOS_DIR / logo_path
        if logo_file.exists():
            try:
                logo = Image(str(logo_file), width=16 * mm, height=16 * mm)
                logo.hAlign = "LEFT"
                logo_cell = logo
            except Exception:
                logo_cell = ""

    company_name = (empresa.get("nome_fantasia") or empresa.get("razao_social") or "ERP Vendas").strip()
    emitted_at = contexto["data_impressao"].strftime("%d/%m/%Y %H:%M") if contexto.get("data_impressao") else "-"
    usuario = session.get("nome") or "-"
    filtro = contexto.get("termo") or "Todos os produtos"

    left_header = [
        Paragraph(company_name, style_brand),
        Paragraph("Relatorio de Estoque Minimo", style_title),
        Paragraph("Analise profissional de reposicao com base no estoque atual e no estoque minimo cadastrado.", style_normal),
    ]
    right_header = [
        Paragraph("EMISSAO", style_meta_label),
        Paragraph(emitted_at, style_meta_value),
        Paragraph("USUARIO", style_meta_label),
        Paragraph(usuario, style_meta_value),
        Paragraph("FILTRO", style_meta_label),
        Paragraph(filtro, style_meta_value),
    ]

    header_table = Table(
        [[logo_cell, left_header, right_header]],
        colWidths=[20 * mm, 108 * mm, 50 * mm],
    )
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 8),
                ("TOPPADDING", (0, 0), (0, 0), 2),
                ("BOX", (2, 0), (2, 0), 0.8, colors.HexColor("#dbe5ef")),
                ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#f8fbff")),
                ("LEFTPADDING", (2, 0), (2, 0), 10),
                ("RIGHTPADDING", (2, 0), (2, 0), 10),
                ("TOPPADDING", (2, 0), (2, 0), 10),
                ("BOTTOMPADDING", (2, 0), (2, 0), 8),
                ("LEFTPADDING", (1, 0), (1, 0), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 10),
                ("TOPPADDING", (1, 0), (1, 0), 0),
                ("BOTTOMPADDING", (1, 0), (1, 0), 0),
                ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor("#dbe5ef")),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 8))

    metrics = [
        ("PRODUTOS", str(contexto.get("total_produtos", 0))),
        ("ABAIXO DO MINIMO", str(contexto.get("abaixo_minimo", 0))),
        ("NO IDEAL", str(contexto.get("no_minimo", 0))),
        ("SUGESTAO DE COMPRA", f"{float(contexto.get('total_sugerido', 0) or 0):.2f}"),
    ]
    metric_cells = []
    for label, value in metrics:
        metric_cells.append([Paragraph(label, style_metric_label), Paragraph(value, style_metric_value)])
    metrics_table = Table([metric_cells], colWidths=[doc.width / 4.0] * 4)
    metrics_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#dbe5ef")),
                ("INNERGRID", (0, 0), (-1, -1), 0.8, colors.HexColor("#dbe5ef")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fbff")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(metrics_table)
    story.append(Spacer(1, 10))

    table_rows = [[
        Paragraph("Codigo", style_small_label),
        Paragraph("Produto", style_small_label),
        Paragraph("Familia", style_small_label),
        Paragraph("Und", style_small_label),
        Paragraph("Atual", style_small_label),
        Paragraph("Minimo", style_small_label),
        Paragraph("Saldo", style_small_label),
        Paragraph("Sugestao", style_small_label),
        Paragraph("Status", style_small_label),
    ]]

    for produto in contexto.get("produtos", []):
        status = "Comprar" if produto["status_estoque"] == "abaixo" else "Perfeito" if produto["status_estoque"] == "ideal" else "Acima do minimo"
        table_rows.append([
            Paragraph(produto.get("codigo") or "-", style_small_value),
            Paragraph(produto.get("descricao") or "-", style_small_value),
            Paragraph(produto.get("categoria") or "-", style_small_value),
            Paragraph(produto.get("unidade") or "-", style_small_value),
            Paragraph(f"{float(produto.get('estoque_atual') or 0):.2f}", style_right),
            Paragraph(f"{float(produto.get('estoque_minimo') or 0):.2f}", style_right),
            Paragraph(f"{float(produto.get('saldo_minimo') or 0):.2f}", style_right),
            Paragraph(f"{float(produto.get('sugestao_compra') or 0):.2f}", style_right),
            Paragraph(status, style_small_value),
        ])

    if len(table_rows) == 1:
        table_rows.append([Paragraph("Nenhum produto encontrado para o filtro informado.", style_small_value)] + [""] * 8)

    report_table = Table(
        table_rows,
        repeatRows=1,
        colWidths=[18 * mm, 44 * mm, 30 * mm, 12 * mm, 18 * mm, 18 * mm, 18 * mm, 20 * mm, 24 * mm],
    )
    report_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf3f8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#486581")),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#dbe5ef")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfdff")]),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(report_table)
    story.append(Spacer(1, 8))

    def draw_watermark(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 34)
        canvas.setFillColor(colors.Color(16 / 255, 42 / 255, 67 / 255, alpha=0.08))
        canvas.translate(document.pagesize[0] / 2, document.pagesize[1] / 2)
        canvas.rotate(35)
        canvas.drawCentredString(0, 0, "@rbcorp")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_watermark, onLaterPages=draw_watermark)
    buffer.seek(0)
    return buffer


@app.route("/produtos/novo", methods=["POST"])
@login_required
@screen_required("produtos")
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
@screen_required("familias_produtos")
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
@screen_required("familias_produtos")
def familias_produtos():
    familias = get_familias_produtos(apenas_ativas=False)
    return render_template("familias_produtos.html", familias=familias)


@app.route("/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
@login_required
@screen_required("produtos")
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
@screen_required("produtos")
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
@screen_required("condicoes_pagamento")
def condicoes_pagamento():
    termo = request.args.get("q", "").strip()
    where = ""
    params = ()
    if termo:
        where = """
        WHERE nome ILIKE %s
           OR COALESCE(forma_pagamento, '') ILIKE %s
           OR COALESCE(finalidade, 'AMBOS') ILIKE %s
        """
        params = (f"%{termo}%", f"%{termo}%", f"%{termo}%")

    rows = fetch_all(
        f"""
        SELECT
            id, nome, forma_pagamento, parcelas,
            dias_intervalo, taxa_percentual,
            COALESCE(finalidade, 'AMBOS') AS finalidade,
            ativo, created_at
        FROM public.condicoes_pagamento
        {where}
        ORDER BY nome
        """,
        params,
    )
    return render_template("condicoes_pagamento.html", condicoes=rows, q=termo)


@app.route("/condicoes-pagamento/nova", methods=["POST"])
@login_required
@screen_required("condicoes_pagamento")
def condicoes_pagamento_nova():
    nome = request.form.get("nome", "").strip()
    forma_pagamento = request.form.get("forma_pagamento", "").strip()
    finalidade = (request.form.get("finalidade") or "AMBOS").strip().upper()
    parcelas = int(request.form.get("parcelas", "1") or 1)
    dias_intervalo = int(request.form.get("dias_intervalo", "30") or 30)
    taxa_percentual = request.form.get("taxa_percentual", "0").strip() or "0"
    ativo = "ativo" in request.form

    if not nome or not forma_pagamento:
        flash("Informe nome e forma de pagamento.", "warning")
        return redirect(url_for("condicoes_pagamento"))
    if finalidade not in ("VENDA", "COMPRA", "AMBOS"):
        flash("Selecione uma finalidade válida.", "warning")
        return redirect(url_for("condicoes_pagamento"))

    if parcelas < 1:
        parcelas = 1

    if dias_intervalo < 0:
        dias_intervalo = 0

    try:
        execute(
            """
            INSERT INTO public.condicoes_pagamento
            (nome, forma_pagamento, parcelas, dias_intervalo, taxa_percentual, finalidade, ativo)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (nome, forma_pagamento, parcelas, dias_intervalo, taxa_percentual, finalidade, ativo),
        )
        flash("CondiÃ§Ã£o de pagamento cadastrada com sucesso.", "success")
    except psycopg2.Error as e:
        flash(f"Erro ao cadastrar condiÃ§Ã£o: {e.pgerror or str(e)}", "danger")

    return redirect(url_for("condicoes_pagamento"))


@app.route("/condicoes-pagamento/<int:condicao_id>/editar", methods=["POST"])
@login_required
@screen_required("condicoes_pagamento")
def condicoes_pagamento_editar(condicao_id):
    nome = request.form.get("nome", "").strip()
    forma_pagamento = request.form.get("forma_pagamento", "").strip()
    finalidade = (request.form.get("finalidade") or "AMBOS").strip().upper()
    parcelas = int(request.form.get("parcelas", "1") or 1)
    dias_intervalo = int(request.form.get("dias_intervalo", "30") or 30)
    taxa_percentual = request.form.get("taxa_percentual", "0").strip() or "0"
    ativo = "ativo" in request.form

    if not nome or not forma_pagamento:
        flash("Informe nome e forma de pagamento.", "warning")
        return redirect(url_for("condicoes_pagamento"))
    if finalidade not in ("VENDA", "COMPRA", "AMBOS"):
        flash("Selecione uma finalidade válida.", "warning")
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
                   finalidade = %s,
                   ativo = %s
             WHERE id = %s
            """,
            (
                nome,
                forma_pagamento,
                parcelas,
                dias_intervalo,
                taxa_percentual,
                finalidade,
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
@screen_required("vendas")
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

    clientes = get_pessoas_por_cadastro("CLIENTE")
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
@screen_required("vendas")
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

    condicoes = get_condicoes_pagamento_por_finalidade("VENDA")

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
                      AND COALESCE(finalidade, 'AMBOS') IN ('VENDA', 'AMBOS')
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
@screen_required("vendas")
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

    clientes = get_pessoas_por_cadastro("CLIENTE")
    produtos = fetch_all(
        """
        SELECT id, descricao, unidade, preco_venda, estoque_atual, ativo
        FROM public.produtos
        WHERE ativo = TRUE
        ORDER BY descricao
        """
    )
    condicoes = get_condicoes_pagamento_por_finalidade("VENDA")

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
                      AND COALESCE(finalidade, 'AMBOS') IN ('VENDA', 'AMBOS')
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
@screen_required("vendas")
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
@screen_required("vendas")
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


# =========================================================
# COMPRAS / ENTRADA DE NOTA
# =========================================================
@app.route("/entradas-notas")
@login_required
@screen_required("entradas_notas")
def entradas_notas():
    fornecedor_id_raw = (request.args.get("fornecedor_id") or "").strip()
    data_ini = (request.args.get("data_ini") or "").strip()
    data_fim = (request.args.get("data_fim") or "").strip()
    where = []
    params = []
    fornecedor_id = None
    fornecedor_nome = ""

    if fornecedor_id_raw:
        try:
            fornecedor_id = int(fornecedor_id_raw)
        except ValueError:
            fornecedor_id = None

    if fornecedor_id:
        where.append("c.fornecedor_id = %s")
        params.append(fornecedor_id)
        fornecedor = fetch_one(
            """
            SELECT nome
            FROM public.pessoas
            WHERE id = %s
            """,
            (fornecedor_id,),
        )
        fornecedor_nome = fornecedor["nome"] if fornecedor else ""
    if data_ini:
        where.append("DATE(c.data_emissao) >= %s")
        params.append(data_ini)
    if data_fim:
        where.append("DATE(c.data_emissao) <= %s")
        params.append(data_fim)

    query = """
        SELECT
            c.id,
            c.numero_nota,
            c.serie,
            c.data_emissao,
            c.valor_produtos,
            c.desconto,
            c.acrescimo,
            c.valor_total,
            c.parcelas,
            COALESCE(c.status, 'ABERTA') AS status,
            p.nome AS fornecedor,
            cp.nome AS condicao_pagamento
        FROM public.compras c
        LEFT JOIN public.pessoas p ON p.id = c.fornecedor_id
        LEFT JOIN public.condicoes_pagamento cp ON cp.id = c.condicao_pagamento_id
    """
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY c.id DESC"

    compras = fetch_all(query, tuple(params))
    usuario = get_user_operational_permissions(session["user_id"])
    fornecedores = get_pessoas_por_cadastro("FORNECEDOR")
    return render_template(
        "entradas_notas.html",
        compras=compras,
        fornecedores=fornecedores,
        fornecedor_id=fornecedor_id,
        fornecedor_nome=fornecedor_nome,
        data_ini=data_ini,
        data_fim=data_fim,
        permite_editar_compra=bool(usuario.get("permite_editar_compra")),
        permite_excluir_compra=bool(usuario.get("permite_excluir_compra")),
        permite_estornar_compra=bool(usuario.get("permite_estornar_compra")),
        permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")),
    )


@app.route("/entradas-notas/nova", methods=["GET", "POST"])
@login_required
@screen_required("entradas_notas")
def entradas_notas_nova():
    fornecedores = get_pessoas_por_cadastro("FORNECEDOR")
    produtos = fetch_all(
        """
        SELECT id, descricao, unidade, custo, estoque_atual
        FROM public.produtos
        WHERE ativo = TRUE
        ORDER BY descricao
        """
    )
    condicoes = get_condicoes_pagamento_por_finalidade("COMPRA")
    usuario = get_user_operational_permissions(session["user_id"])

    if request.method == "POST":
        acao = (request.form.get("acao") or "FINALIZADA").strip().upper()
        fornecedor_id = request.form.get("fornecedor_id") or None
        numero_nota = (request.form.get("numero_nota") or "").strip()
        serie = (request.form.get("serie") or "").strip()
        data_emissao = parse_date_value(request.form.get("data_emissao"), date.today())
        observacoes = (request.form.get("observacoes") or "").strip()
        desconto = parse_decimal(request.form.get("desconto") or "0")
        acrescimo = parse_decimal(request.form.get("acrescimo") or "0")

        produto_ids = request.form.getlist("produto_id[]")
        quantidades = request.form.getlist("quantidade[]")
        valores = request.form.getlist("valor_unitario[]")

        if not fornecedor_id:
            flash("Selecione o fornecedor da nota.", "warning")
            return render_template("entrada_nota_nova.html", fornecedores=fornecedores, produtos=produtos, condicoes=condicoes, permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")))
        if not numero_nota:
            flash("Informe o número da nota.", "warning")
            return render_template("entrada_nota_nova.html", fornecedores=fornecedores, produtos=produtos, condicoes=condicoes, permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")))
        if acao not in ("ABERTA", "FINALIZADA"):
            acao = "FINALIZADA"

        itens = []
        valor_produtos = 0.0

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
                    flash("Um dos produtos informados não foi encontrado.", "danger")
                    return render_template("entrada_nota_nova.html", fornecedores=fornecedores, produtos=produtos, condicoes=condicoes, permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")))

                quantidade = parse_quantity_by_unidade(produto_db["unidade"], quantidades[i] if i < len(quantidades) else "0")
                valor_unitario = parse_decimal(valores[i] if i < len(valores) else "0")
                total_item = float(quantidade) * float(valor_unitario)

                if quantidade <= 0:
                    continue
                if valor_unitario < 0:
                    flash("Existe item com valor unitário inválido.", "warning")
                    return render_template("entrada_nota_nova.html", fornecedores=fornecedores, produtos=produtos, condicoes=condicoes, permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")))

                valor_produtos += total_item
                itens.append(
                    {
                        "produto_id": produto_id,
                        "quantidade": quantidade,
                        "valor_unitario": valor_unitario,
                        "total": total_item,
                        "estoque_atual": float(produto_db["estoque_atual"] or 0),
                    }
                )

            if not itens:
                flash("Adicione pelo menos um item na nota.", "warning")
                return render_template("entrada_nota_nova.html", fornecedores=fornecedores, produtos=produtos, condicoes=condicoes, permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")))

            valor_total = max(float(valor_produtos) - float(desconto) + float(acrescimo), 0.0)
            condicao, faturas = parse_faturas_compra(request.form, data_emissao, condicoes)
            total_pagamentos = sum(float(f["valor"] or 0) for f in faturas)

            if not condicao:
                flash("Selecione a condição de pagamento da compra.", "warning")
                return render_template("entrada_nota_nova.html", fornecedores=fornecedores, produtos=produtos, condicoes=condicoes, permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")))
            if not faturas:
                flash("Gere ou informe pelo menos uma fatura para a compra.", "warning")
                return render_template("entrada_nota_nova.html", fornecedores=fornecedores, produtos=produtos, condicoes=condicoes, permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")))
            if round(total_pagamentos, 2) != round(valor_total, 2):
                flash(f"As faturas somam R$ {total_pagamentos:.2f} e a nota totaliza R$ {valor_total:.2f}. Ajuste antes de salvar.", "danger")
                return render_template("entrada_nota_nova.html", fornecedores=fornecedores, produtos=produtos, condicoes=condicoes, permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")))

            conn = get_conn()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        INSERT INTO public.compras (
                            fornecedor_id,
                            usuario_id,
                            numero_nota,
                            serie,
                            data_emissao,
                            valor_produtos,
                            desconto,
                            acrescimo,
                            valor_total,
                            forma_pagamento,
                            parcelas,
                            valor_parcela,
                            condicao_pagamento_id,
                            observacoes,
                            status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            int(fornecedor_id),
                            session["user_id"],
                            numero_nota,
                            serie or None,
                            data_emissao,
                            valor_produtos,
                            desconto,
                            acrescimo,
                            valor_total,
                            condicao["forma_pagamento"],
                            len(faturas),
                            float(valor_total) / max(len(faturas), 1),
                            condicao["id"],
                            observacoes,
                            acao,
                        ),
                    )
                    compra_id = cur.fetchone()["id"]

                    for item in itens:
                        cur.execute(
                            """
                            INSERT INTO public.compra_itens (
                                compra_id,
                                produto_id,
                                quantidade,
                                valor_unitario,
                                total
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (
                                compra_id,
                                item["produto_id"],
                                item["quantidade"],
                                item["valor_unitario"],
                                item["total"],
                            ),
                        )
                    replace_compra_financeiro(cur, compra_id, int(fornecedor_id), numero_nota, faturas, acao)
                    if acao == "FINALIZADA":
                        aplicar_finalizacao_compra(cur, compra_id, numero_nota)

                conn.commit()
                flash("Entrada de nota cadastrada com sucesso.", "success")
                return redirect(url_for("entradas_notas"))
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        except ValueError:
            flash("Valores inválidos na entrada de nota.", "danger")

    return render_template("entrada_nota_nova.html", fornecedores=fornecedores, produtos=produtos, condicoes=condicoes, permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")))


@app.route("/entradas-notas/<int:compra_id>/editar", methods=["GET", "POST"])
@login_required
@screen_required("entradas_notas")
def entradas_notas_editar(compra_id):
    usuario = get_user_operational_permissions(session["user_id"])
    if not usuario.get("permite_editar_compra"):
        flash("Você não tem permissão para editar notas de entrada.", "danger")
        return redirect(url_for("entradas_notas"))

    compra = fetch_one(
        """
        SELECT *
        FROM public.compras
        WHERE id = %s
        """,
        (compra_id,),
    )
    if not compra:
        flash("Nota não encontrada.", "warning")
        return redirect(url_for("entradas_notas"))
    if (compra.get("status") or "ABERTA") == "ESTORNADA":
        flash("Notas estornadas não podem ser editadas.", "warning")
        return redirect(url_for("entradas_notas"))
    pode_alterar, bloqueios = compra_pode_alterar_estoque(compra_id, compra.get("status"))
    if not pode_alterar:
        produto = bloqueios[0].get("produto") if bloqueios else "um dos produtos"
        flash(f"A nota não pode ser alterada porque o produto {produto} já teve movimentação após esta entrada.", "warning")
        return redirect(url_for("entradas_notas"))

    fornecedores = get_pessoas_por_cadastro("FORNECEDOR")
    produtos = fetch_all(
        """
        SELECT id, descricao, unidade, custo, estoque_atual
        FROM public.produtos
        WHERE ativo = TRUE
        ORDER BY descricao
        """
    )
    condicoes = get_condicoes_pagamento_por_finalidade("COMPRA")

    if request.method == "POST":
        fornecedor_id = request.form.get("fornecedor_id") or None
        numero_nota = (request.form.get("numero_nota") or "").strip()
        serie = (request.form.get("serie") or "").strip()
        data_emissao = parse_date_value(request.form.get("data_emissao"), date.today())
        observacoes = (request.form.get("observacoes") or "").strip()
        desconto = parse_decimal(request.form.get("desconto") or "0")
        acrescimo = parse_decimal(request.form.get("acrescimo") or "0")
        acao = (request.form.get("acao") or "ABERTA").strip().upper()
        produto_ids = request.form.getlist("produto_id[]")
        quantidades = request.form.getlist("quantidade[]")
        valores = request.form.getlist("valor_unitario[]")

        itens = []
        valor_produtos = 0.0
        for i in range(len(produto_ids)):
            if not produto_ids[i]:
                continue
            produto_id = int(produto_ids[i])
            produto_db = fetch_one(
                "SELECT id, descricao, unidade FROM public.produtos WHERE id = %s",
                (produto_id,),
            )
            if not produto_db:
                flash("Um dos produtos informados não foi encontrado.", "danger")
                return redirect(url_for("entradas_notas_editar", compra_id=compra_id))
            quantidade = parse_quantity_by_unidade(produto_db["unidade"], quantidades[i] if i < len(quantidades) else "0")
            valor_unitario = parse_decimal(valores[i] if i < len(valores) else "0")
            if quantidade <= 0:
                continue
            total_item = float(quantidade) * float(valor_unitario)
            valor_produtos += total_item
            itens.append({"produto_id": produto_id, "quantidade": quantidade, "valor_unitario": valor_unitario, "total": total_item})

        valor_total = max(float(valor_produtos) - float(desconto) + float(acrescimo), 0.0)
        condicao, faturas = parse_faturas_compra(request.form, data_emissao, condicoes)
        total_pagamentos = sum(float(f["valor"] or 0) for f in faturas)
        if not fornecedor_id or not numero_nota or not itens or not condicao or not faturas:
            flash("Preencha fornecedor, nota, itens e faturas.", "warning")
            return redirect(url_for("entradas_notas_editar", compra_id=compra_id))
        if round(total_pagamentos, 2) != round(valor_total, 2):
            flash("As faturas precisam fechar o valor total da nota.", "warning")
            return redirect(url_for("entradas_notas_editar", compra_id=compra_id))

        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                status_atual = (compra.get("status") or "ABERTA").upper()
                if status_atual in ("FINALIZADA", "ATIVA"):
                    reverter_finalizacao_compra(cur, compra_id, compra["numero_nota"], "AJUSTE_COMPRA")
                cur.execute(
                    """
                    UPDATE public.compras
                       SET fornecedor_id = %s,
                           numero_nota = %s,
                           serie = %s,
                           data_emissao = %s,
                           valor_produtos = %s,
                           desconto = %s,
                           acrescimo = %s,
                           valor_total = %s,
                           forma_pagamento = %s,
                           parcelas = %s,
                           valor_parcela = %s,
                           condicao_pagamento_id = %s,
                           observacoes = %s,
                           status = %s
                     WHERE id = %s
                    """,
                    (
                        int(fornecedor_id),
                        numero_nota,
                        serie or None,
                        data_emissao,
                        valor_produtos,
                        desconto,
                        acrescimo,
                        valor_total,
                        condicao["forma_pagamento"],
                        len(faturas),
                        float(valor_total) / max(len(faturas), 1),
                        condicao["id"],
                        observacoes,
                        acao,
                        compra_id,
                    ),
                )
                cur.execute("DELETE FROM public.compra_itens WHERE compra_id = %s", (compra_id,))
                for item in itens:
                    cur.execute(
                        """
                        INSERT INTO public.compra_itens (compra_id, produto_id, quantidade, valor_unitario, total)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (compra_id, item["produto_id"], item["quantidade"], item["valor_unitario"], item["total"]),
                    )
                replace_compra_financeiro(cur, compra_id, int(fornecedor_id), numero_nota, faturas, acao)
                if acao == "FINALIZADA":
                    aplicar_finalizacao_compra(cur, compra_id, numero_nota)
                log_financeiro("EDICAO_NOTA", f"Nota {numero_nota} editada.", compra_id=compra_id, cur=cur)

            conn.commit()
            flash("Nota atualizada com sucesso.", "success")
            return redirect(url_for("entradas_notas"))
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    itens = fetch_all(
        """
        SELECT produto_id, quantidade, valor_unitario
        FROM public.compra_itens
        WHERE compra_id = %s
        ORDER BY id
        """,
        (compra_id,),
    )
    faturas = fetch_all(
        """
        SELECT
            numero_parcela,
            descricao,
            valor,
            data_vencimento
        FROM public.contas_pagar
        WHERE compra_id = %s
        ORDER BY numero_parcela, id
        """,
        (compra_id,),
    )
    return render_template(
        "entrada_nota_nova.html",
        fornecedores=fornecedores,
        produtos=produtos,
        condicoes=condicoes,
        compra=compra,
        itens_iniciais=itens,
        faturas_iniciais=faturas,
        permite_editar_compra=bool(usuario.get("permite_editar_compra")),
        permite_editar_financeiro=bool(usuario.get("permite_editar_financeiro")),
    )


@app.route("/entradas-notas/<int:compra_id>/excluir", methods=["POST"])
@login_required
@screen_required("entradas_notas")
def entradas_notas_excluir(compra_id):
    usuario = get_user_operational_permissions(session["user_id"])
    if not usuario.get("permite_excluir_compra"):
        flash("Você não tem permissão para excluir notas de entrada.", "danger")
        return redirect(url_for("entradas_notas"))

    compra = fetch_one(
        """
        SELECT id, numero_nota, COALESCE(status, 'ABERTA') AS status
        FROM public.compras
        WHERE id = %s
        """,
        (compra_id,),
    )
    if not compra:
        flash("Nota não encontrada.", "warning")
        return redirect(url_for("entradas_notas"))
    if compra["status"] == "ESTORNADA":
        flash("Notas estornadas não podem ser excluídas.", "warning")
        return redirect(url_for("entradas_notas"))

    pode_alterar, bloqueios = compra_pode_alterar_estoque(compra_id, compra.get("status"))
    if not pode_alterar:
        produto = bloqueios[0].get("produto") if bloqueios else "um dos produtos"
        flash(f"A nota não pode ser excluída porque o produto {produto} já teve movimentação após esta entrada.", "warning")
        return redirect(url_for("entradas_notas"))

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if compra["status"] in ("FINALIZADA", "ATIVA"):
                reverter_finalizacao_compra(cur, compra_id, compra["numero_nota"], "EXCLUSAO_COMPRA")
            log_financeiro("EXCLUSAO_NOTA", f"Nota {compra['numero_nota']} excluída.", compra_id=compra_id, cur=cur)
            cur.execute("DELETE FROM public.compras WHERE id = %s", (compra_id,))
        conn.commit()
        flash("Nota excluída com sucesso.", "success")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return redirect(url_for("entradas_notas"))


@app.route("/entradas-notas/<int:compra_id>/estornar", methods=["POST"])
@login_required
@screen_required("entradas_notas")
def entradas_notas_estornar(compra_id):
    usuario = get_user_operational_permissions(session["user_id"])
    if not usuario.get("permite_estornar_compra"):
        flash("Você não tem permissão para estornar notas.", "danger")
        return redirect(url_for("entradas_notas"))

    compra = fetch_one(
        """
        SELECT id, numero_nota, COALESCE(status, 'ABERTA') AS status
        FROM public.compras
        WHERE id = %s
        """,
        (compra_id,),
    )
    if not compra:
        flash("Nota não encontrada.", "warning")
        return redirect(url_for("entradas_notas"))
    if compra["status"] not in ("FINALIZADA", "ATIVA"):
        flash("Apenas notas finalizadas podem ser estornadas.", "warning")
        return redirect(url_for("entradas_notas"))

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            reverter_finalizacao_compra(cur, compra_id, compra["numero_nota"], "ESTORNO_COMPRA")
            cur.execute(
                """
                UPDATE public.compras
                   SET status = 'ESTORNADA'
                 WHERE id = %s
                """,
                (compra_id,),
            )
            cur.execute(
                "UPDATE public.contas_pagar SET status = 'ESTORNADO' WHERE compra_id = %s",
                (compra_id,),
            )
            cur.execute(
                "UPDATE public.compras_pagamentos SET status = 'ESTORNADO' WHERE compra_id = %s",
                (compra_id,),
            )
            log_financeiro("ESTORNO_NOTA", f"Nota {compra['numero_nota']} estornada.", compra_id=compra_id, cur=cur)
        conn.commit()
        flash("Nota estornada com sucesso.", "success")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return redirect(url_for("entradas_notas"))


# =========================================================
# CONTAS A PAGAR
# =========================================================
@app.route("/contas-pagar")
@login_required
@screen_required("contas_pagar")
def contas_pagar():
    fornecedor_id_raw = (request.args.get("fornecedor_id") or "").strip()
    status = (request.args.get("status") or "").strip().upper()
    data_ini = (request.args.get("data_ini") or "").strip()
    data_fim = (request.args.get("data_fim") or "").strip()

    where = []
    params = []
    fornecedor_id = None

    if fornecedor_id_raw:
        try:
            fornecedor_id = int(fornecedor_id_raw)
        except ValueError:
            fornecedor_id = None

    if fornecedor_id:
        where.append("cp.fornecedor_id = %s")
        params.append(fornecedor_id)
    if status:
        where.append("COALESCE(cp.status, 'PENDENTE') = %s")
        params.append(status)
    else:
        where.append("COALESCE(cp.status, 'PENDENTE') NOT IN ('RASCUNHO', 'ESTORNADO')")
    if data_ini:
        where.append("cp.data_vencimento >= %s")
        params.append(data_ini)
    if data_fim:
        where.append("cp.data_vencimento <= %s")
        params.append(data_fim)

    query = """
        SELECT
            cp.id,
            cp.compra_id,
            cp.numero_parcela,
            cp.descricao,
            cp.valor,
            cp.data_vencimento,
            cp.data_pagamento,
            cp.status,
            p.nome AS fornecedor,
            c.numero_nota
        FROM public.contas_pagar cp
        LEFT JOIN public.pessoas p ON p.id = cp.fornecedor_id
        LEFT JOIN public.compras c ON c.id = cp.compra_id
    """
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY cp.data_vencimento ASC, cp.id ASC"

    contas = fetch_all(query, tuple(params))
    resumo = fetch_one(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN COALESCE(cp.status, 'PENDENTE') = 'PENDENTE' THEN cp.valor ELSE 0 END), 0) AS total_pendente,
            COALESCE(SUM(CASE WHEN COALESCE(cp.status, 'PENDENTE') = 'PAGO' THEN cp.valor ELSE 0 END), 0) AS total_pago
        FROM public.contas_pagar cp
        {(' WHERE ' + ' AND '.join(where)) if where else ''}
        """,
        tuple(params),
    ) or {"total_pendente": 0, "total_pago": 0}

    usuario = get_user_operational_permissions(session["user_id"])
    return render_template(
        "contas_pagar.html",
        contas=contas,
        fornecedores=get_pessoas_por_cadastro("FORNECEDOR"),
        fornecedor_id=fornecedor_id,
        status=status,
        data_ini=data_ini,
        data_fim=data_fim,
        total_pendente=float(resumo["total_pendente"] or 0),
        total_pago=float(resumo["total_pago"] or 0),
        hoje=date.today(),
        permite_baixar_contas_pagar=bool(usuario.get("permite_baixar_contas_pagar")),
    )


@app.route("/contas-pagar/<int:conta_id>/baixar", methods=["POST"])
@login_required
@screen_required("contas_pagar")
def contas_pagar_baixar(conta_id):
    usuario = get_user_operational_permissions(session["user_id"])
    if not usuario.get("permite_baixar_contas_pagar"):
        flash("Você não tem permissão para baixar contas a pagar.", "danger")
        return redirect(url_for("contas_pagar"))
    conta = fetch_one(
        """
        SELECT id, compra_id, pagamento_id, COALESCE(status, 'PENDENTE') AS status
        FROM public.contas_pagar
        WHERE id = %s
        """,
        (conta_id,),
    )
    if not conta:
        flash("Conta a pagar não encontrada.", "warning")
        return redirect(url_for("contas_pagar"))
    if conta["status"] == "PAGO":
        flash("Esta conta já foi baixada.", "info")
        return redirect(url_for("contas_pagar"))
    if conta["status"] == "ESTORNADO":
        flash("Não é possível baixar uma conta estornada.", "warning")
        return redirect(url_for("contas_pagar"))

    data_pagamento = parse_date_value(request.form.get("data_pagamento"), date.today())
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE public.contas_pagar
                   SET status = 'PAGO',
                       data_pagamento = %s
                 WHERE id = %s
                """,
                (data_pagamento, conta_id),
            )
            if conta.get("pagamento_id"):
                cur.execute(
                    """
                    UPDATE public.compras_pagamentos
                       SET status = 'PAGO'
                     WHERE id = %s
                    """,
                    (conta["pagamento_id"],),
                )
            log_financeiro("BAIXA_CONTA_PAGAR", f"Conta {conta_id} baixada em {data_pagamento.strftime('%d/%m/%Y')}.", conta_pagar_id=conta_id, compra_id=conta.get("compra_id"), cur=cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    flash("Conta baixada com sucesso.", "success")
    return redirect(url_for("contas_pagar"))


@app.route("/caixa")
@login_required
@screen_required("caixa")
def caixa():
    usuario = get_user_operational_permissions(session["user_id"])
    empresa = get_empresa_configuracoes()
    return render_template(
        "caixa.html",
        empresa=empresa,
        pode_abrir=bool(usuario.get("permite_abrir_caixa")),
        pode_fechar=bool(usuario.get("permite_fechar_caixa")),
        pode_suprimento=bool(usuario.get("permite_suprimento_caixa")),
        pode_sangria=bool(usuario.get("permite_sangria_caixa")),
        pode_receber=bool(usuario.get("permite_receber_venda_caixa")),
    )


@app.route("/contas-receber")
@login_required
@screen_required("contas_receber")
def contas_receber():
    usuario = get_user_operational_permissions(session["user_id"])
    empresa = get_empresa_configuracoes()
    return render_template(
        "contas_receber.html",
        empresa=empresa,
        pode_baixar=bool(usuario.get("permite_baixar_contas_receber")),
    )


@app.route("/balancete")
@login_required
@screen_required("balancete")
def balancete():
    usuario = get_user_operational_permissions(session["user_id"])
    empresa = get_empresa_configuracoes()
    return render_template(
        "balancete.html",
        empresa=empresa,
        pode_ver_balancete=bool(usuario.get("permite_ver_balancete")),
    )


@app.route("/empresas")
@login_required
@screen_required("empresas")
def empresas():
    rows = fetch_all(
        """
        SELECT
            id, nome_fantasia, razao_social, cnpj, telefone, email,
            cidade, estado, responsavel,
            COALESCE(modo_operacao, 'PDV') AS modo_operacao,
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
@screen_required("empresas")
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
        modo_operacao = (request.form.get("modo_operacao") or "PDV").strip().upper()
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
        if modo_operacao not in ("PDV", "FRENTE_LOJA"):
            flash("Selecione um modo de operação válido.", "warning")
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
                        cidade, estado, responsavel, modo_operacao,
                        produto_codigo_obrigatorio, produto_familia_obrigatoria, produto_unidade_obrigatoria,
                        produto_custo_obrigatorio, produto_preco_venda_obrigatorio, produto_estoque_atual_obrigatorio,
                        produto_estoque_minimo_obrigatorio,
                        cliente_tipo_obrigatorio, cliente_nome_obrigatorio, cliente_documento_obrigatorio,
                        cliente_telefone_obrigatorio, cliente_email_obrigatorio, cliente_cep_obrigatorio,
                        cliente_endereco_obrigatorio, cliente_cidade_obrigatorio, cliente_estado_obrigatorio,
                        cliente_observacoes_obrigatorio,
                        permite_estoque_negativo, permite_desconto, ativo
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        nome_fantasia, razao_social, cnpj, telefone, email,
                        cidade, estado, responsavel, modo_operacao,
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
@screen_required("empresas")
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
            COALESCE(modo_operacao, 'PDV') AS modo_operacao,
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
        modo_operacao = (request.form.get("modo_operacao") or "PDV").strip().upper()
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
                "modo_operacao": modo_operacao,
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
                "modo_operacao": modo_operacao,
            })
            return render_template("empresa_form.html", empresa=empresa)
        if modo_operacao not in ("PDV", "FRENTE_LOJA"):
            flash("Selecione um modo de operação válido.", "warning")
            empresa["modo_operacao"] = modo_operacao
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
                           modo_operacao = %s,
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
                        cidade, estado, responsavel, modo_operacao,
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
@screen_required("produtos")
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


