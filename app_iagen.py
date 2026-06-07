import json
import unicodedata
import pandas as pd
import streamlit as st
import boto3
from sqlalchemy import create_engine, text

# =====================================================
# CONFIGURAÇÕES DO BANCO
# =====================================================

DB_USER = st.secrets["DB_USER"]
DB_PASSWORD = st.secrets["DB_PASSWORD"]
DB_HOST = st.secrets["DB_HOST"]
DB_PORT = st.secrets["DB_PORT"]
DB_NAME = st.secrets["DB_NAME"]

# =====================================================
# CONFIGURAÇÕES AWS BEDROCK
# =====================================================

AWS_ACCESS_KEY_ID = st.secrets["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = st.secrets["AWS_SECRET_ACCESS_KEY"]
AWS_SESSION_TOKEN = st.secrets["AWS_SESSION_TOKEN"]
AWS_REGION = st.secrets["AWS_REGION"]
MODEL_ID = st.secrets["MODEL_ID"]

# =====================================================
# STREAMLIT
# =====================================================

st.set_page_config(
    page_title="IA Censo Escolar INEP",
    layout="wide"
)

# =====================================================
# ESTILO VISUAL
# =====================================================

st.markdown("""
<style>
.stApp {
    background:
        radial-gradient(circle at top right, rgba(124,58,237,0.45), transparent 30%),
        radial-gradient(circle at bottom left, rgba(37,99,235,0.30), transparent 35%),
        linear-gradient(135deg, #020617 0%, #0f172a 45%, #111827 100%);
    color: #f8fafc;
}

.block-container {
    padding-top: 2rem;
    max-width: 1350px;
}

.app-title {
    font-size: 52px;
    font-weight: 900;
    color: white;
    margin-bottom: 6px;
}

.app-subtitle {
    font-size: 20px;
    color: #cbd5e1;
    margin-bottom: 30px;
}

.glass-card {
    background: rgba(15, 23, 42, 0.78);
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-radius: 24px;
    padding: 24px;
    box-shadow: 0 20px 55px rgba(0,0,0,0.42);
    backdrop-filter: blur(16px);
    margin-bottom: 22px;
}

.chat-card {
    border: 1px solid rgba(139,92,246,0.75);
    box-shadow: 0 0 22px rgba(124,58,237,0.45), 0 25px 65px rgba(0,0,0,0.50);
}

.metric-card {
    border-radius: 22px;
    padding: 22px;
    background: linear-gradient(145deg, rgba(37,99,235,0.25), rgba(124,58,237,0.22));
    border: 1px solid rgba(255,255,255,0.16);
    box-shadow: 0 18px 45px rgba(0,0,0,0.38);
}

.metric-value {
    font-size: 30px;
    font-weight: 900;
    color: white;
}

.metric-label {
    color: #cbd5e1;
    font-size: 14px;
}

[data-testid="stDataFrame"] {
    background: rgba(15,23,42,0.75);
    border-radius: 18px;
    padding: 12px;
    box-shadow: 0 18px 40px rgba(0,0,0,0.35);
}

[data-testid="stChatMessage"] {
    background: rgba(30,41,59,0.78);
    border-radius: 18px;
    padding: 12px;
    box-shadow: 0 12px 32px rgba(0,0,0,0.30);
}

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #020617, #0f172a);
}

.stButton > button {
    background: linear-gradient(90deg, #7c3aed, #2563eb);
    color: white;
    border: none;
    border-radius: 14px;
    font-weight: 800;
    padding: 0.65rem 1rem;
    box-shadow: 0 14px 32px rgba(37,99,235,0.35);
}

.stDownloadButton > button {
    background: linear-gradient(90deg, #059669, #0ea5e9);
    color: white;
    border: none;
    border-radius: 14px;
    font-weight: 800;
}

div[data-testid="stChatInput"] {
    position: relative !important;
    bottom: auto !important;
}
</style>
""", unsafe_allow_html=True)

# =====================================================
# GUARDRAILS SQL
# =====================================================

COMANDOS_PROIBIDOS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "CREATE", "TRUNCATE", "REPLACE", "GRANT",
    "REVOKE", "MERGE", "CALL", "EXEC"
]


def validar_sql_seguro(sql):
    sql_upper = sql.upper().strip()

    if not sql_upper.startswith("SELECT"):
        raise ValueError("Apenas consultas SELECT são permitidas.")

    for comando in COMANDOS_PROIBIDOS:
        if comando in sql_upper:
            raise ValueError(f"Comando SQL proibido detectado: {comando}")

    return sql

def validar_sql_ia(sql):

    sql_upper = sql.upper()

    comandos_bloqueados = [
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "INSERT",
        "CREATE"
    ]

    for cmd in comandos_bloqueados:
        if cmd in sql_upper:
            raise Exception(
                f"Comando proibido: {cmd}"
            )

    if not sql_upper.strip().startswith("SELECT"):
        raise Exception(
            "Somente SELECT permitido."
        )

    return True

def consulta_segura(engine, query):
    query = validar_sql_seguro(query)

    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        return pd.read_sql(text(query), conn)


# =====================================================
# DICIONÁRIO DAS VARIÁVEIS
# =====================================================

DICIONARIO_VARIAVEIS = {
    "nome_municipio": "Nome do Município",
    "nome_uf": "Nome da Unidade Federativa",
    "nome_regiao": "Nome da Região",

    "tp_categoria_escola_privada": "Categoria da Escola Privada",
    "tp_localizacao": "Localização da Escola",
    "tp_localizacao_diferenciada": "Localização Diferenciada da Escola",

    "qt_doc_bas": "Número de Docentes da Educação Básica",
    "qt_doc_inf": "Número de Docentes da Educação Infantil",
    "qt_doc_inf_cre": "Número de Docentes da Educação Infantil - Creche",
    "qt_doc_inf_pre": "Número de Docentes da Educação Infantil - Pré-Escola",
    "qt_doc_med": "Número de Docentes do Ensino Médio Regular",
    "qt_doc_fund": "Número de Docentes do Ensino Fundamental"
}

VARIAVEIS_FINAIS = list(DICIONARIO_VARIAVEIS.keys())

# =====================================================
# FUNÇÕES AUXILIARES
# =====================================================

def limpar_nome_coluna(coluna):
    coluna = str(coluna).strip().lower()
    coluna = unicodedata.normalize("NFKD", coluna)
    coluna = "".join(c for c in coluna if not unicodedata.combining(c))
    coluna = coluna.replace(" ", "_")
    return coluna


def limpar_colunas(df):
    df.columns = [limpar_nome_coluna(c) for c in df.columns]
    return df


@st.cache_resource
def conectar_banco():
    url = (
        f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )

    return create_engine(
        url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True
    )


@st.cache_resource
def conectar_bedrock():
    return boto3.client(
        service_name="bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        aws_session_token=AWS_SESSION_TOKEN
    )


@st.cache_data(show_spinner="Carregando dados do Censo Escolar...")
def carregar_base(limite):
    engine = conectar_banco()

    query = f"""
SELECT
    m.codigo_municipio,
    m.codigo_municipio_dv,
    m.nome_municipio,

    uf.nome_uf,
    r.nome_regiao,

    c.tp_categoria_escola_privada,
    c.tp_localizacao,
    c.tp_localizacao_diferenciada,

    d.qt_doc_bas,
    d.qt_doc_inf,
    d.qt_doc_inf_cre,
    d.qt_doc_inf_pre,
    d.qt_doc_med,
    d.qt_doc_fund

FROM public.inep_censo_escolar c

LEFT JOIN public.inep_censo_escolar_docente d
    ON c.co_entidade = d.co_entidade

LEFT JOIN public.municipio m
    ON CAST(c.co_municipio AS BIGINT) =
       CAST(m.codigo_municipio_dv AS BIGINT)

LEFT JOIN public.unidade_federacao uf
    ON m.cd_uf = uf.cd_uf

LEFT JOIN public.regiao r
    ON uf.cd_regiao = r.cd_regiao

ORDER BY c.co_entidade

LIMIT {int(limite)}
"""

    df = consulta_segura(engine, query)
    df = limpar_colunas(df)

    colunas_existentes = [
        coluna for coluna in VARIAVEIS_FINAIS
        if coluna in df.columns
    ]

    df = df[colunas_existentes].copy()

    return df


def gerar_contexto(df):
    contexto = f"""
Base carregada em modo SOMENTE LEITURA.
Nenhuma alteração no banco de dados é permitida.

Total de registros: {len(df):,}
Total de colunas: {len(df.columns)}

Dicionário das variáveis:
"""

    for coluna, descricao in DICIONARIO_VARIAVEIS.items():
        if coluna in df.columns:
            contexto += f"\n- {coluna}: {descricao}"

    numericas = df.select_dtypes(include=["int64", "float64"]).columns

    if len(numericas) > 0:
        contexto += "\n\nResumo estatístico das variáveis numéricas:\n"
        contexto += df[numericas].describe().to_string()

    # Rankings por município
    if "nome_municipio" in df.columns:
        colunas_rank = [
            "qt_doc_bas",
            "qt_doc_inf",
            "qt_doc_inf_cre",
            "qt_doc_inf_pre",
            "qt_doc_med",
            "qt_doc_fund"
        ]

        for coluna in colunas_rank:
            if coluna in df.columns:
                ranking = (
                    df.groupby("nome_municipio", as_index=False)[coluna]
                    .sum()
                    .sort_values(coluna, ascending=False)
                    .head(10)
                )

                contexto += f"\n\nTop 10 municípios por {coluna}:\n"
                contexto += ranking.to_string(index=False)

    return contexto


def perguntar_ia(pergunta, contexto):
    bedrock = conectar_bedrock()

    prompt = f"""
Você é um especialista em análise de dados educacionais do INEP.

REGRAS OBRIGATÓRIAS:
1. O banco de dados já foi carregado e analisado pelo sistema.
2. Nunca diga que precisa acessar o banco de dados.
3. Nunca diga que não consegue identificar o município se a informação estiver no contexto.
4. Não gere SQL na resposta final.
5. Use os rankings e tabelas do contexto para responder.
6. Responda em português do Brasil.

CONTEXTO:
{contexto}

PERGUNTA:
{pergunta}
"""

    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "inferenceConfig": {
            "maxTokens": 2000,
            "temperature": 0.2
        }
    }

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json"
    )

    response_body = json.loads(response["body"].read())

    return response_body["output"]["message"]["content"][0]["text"]


# =====================================================
# APP
# =====================================================

st.markdown("""
<div class="app-title">📚 IA Generativa - Censo Escolar INEP</div>
<div class="app-subtitle">
Análise inteligente dos dados educacionais com segurança e leitura protegida.
</div>
""", unsafe_allow_html=True)

st.sidebar.header("⚙️ Configurações")

limite = st.sidebar.number_input(
    "Quantidade de linhas para carregar",
    min_value=1000,
    max_value=300000,
    value=30000,
    step=1000
)

if "df" not in st.session_state:
    st.session_state.df = None

if "mensagens" not in st.session_state:
    st.session_state.mensagens = []

if st.sidebar.button("🚀 Carregar base"):
    try:
        with st.spinner("Carregando base..."):
            st.session_state.df = carregar_base(limite)

        st.sidebar.success("Base carregada!")

    except Exception as erro:
        st.error("Erro ao carregar ou processar os dados.")
        st.write(str(erro))


if st.session_state.df is None:
    st.markdown("""
    <div class="glass-card">
        <h2>👋 Bem-vindo</h2>
        <p>Clique em <b>Carregar base</b> na lateral para iniciar a análise.</p>
    </div>
    """, unsafe_allow_html=True)

else:
    df = st.session_state.df

    # =====================================================
    # SOBRE O PROJETO
    # =====================================================

    st.markdown("""
    <div class="glass-card">

    <h3>🎯 Sobre o Projeto</h3>

    <p style="font-size:16px; line-height:1.8;">

    A IA Generativa do Censo Escolar INEP foi desenvolvida para facilitar a exploração e interpretação
    de dados educacionais por meio de perguntas em linguagem natural. A solução integra informações do
    Censo Escolar, municípios, unidades federativas, regiões e indicadores de docentes, permitindo análises
    rápidas e intuitivas sem a necessidade de conhecimentos avançados em bancos de dados.

    O sistema utiliza Inteligência Artificial Generativa para transformar dados em informações relevantes,
    auxiliando na identificação de padrões, comparações e indicadores educacionais. Todas as consultas são
    realizadas em modo seguro e somente leitura, preservando integralmente a base de dados original.

    </p>

    </div>
    """, unsafe_allow_html=True)

    # =====================================================
    # CHAT NO TOPO
    # =====================================================

    st.markdown("""
    <div class="glass-card chat-card">
        <h2>💬 Chat com a IA</h2>
        <p>Faça perguntas sobre docentes, municípios, regiões, localização e categorias escolares.</p>
    </div>
    """, unsafe_allow_html=True)

    with st.container():
        for msg in st.session_state.mensagens:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        pergunta = st.chat_input("Digite sua pergunta e pressione Enter...")

        if pergunta:
            st.session_state.mensagens.append({
                "role": "user",
                "content": pergunta
            })

            with st.chat_message("user"):
                st.write(pergunta)

            with st.chat_message("assistant"):
                with st.spinner("Analisando os dados..."):
                    contexto = gerar_contexto(df)
                    resposta = perguntar_ia(pergunta, contexto)
                    st.write(resposta)

            st.session_state.mensagens.append({
                "role": "assistant",
                "content": resposta
            })

    st.divider()

    # CARDS
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{df.shape[0]:,}</div>
            <div class="metric-label">Linhas carregadas</div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{df.shape[1]}</div>
            <div class="metric-label">Colunas utilizadas</div>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        municipios = df["nome_municipio"].nunique() if "nome_municipio" in df.columns else 0
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{municipios:,}</div>
            <div class="metric-label">Municípios na amostra</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # TABELAS
    col_esq, col_dir = st.columns([1.4, 1])

    with col_esq:
        st.markdown("""
        <div class="glass-card">
            <h3>📌 Prévia da Base Final</h3>
        </div>
        """, unsafe_allow_html=True)

        st.dataframe(df.head(100), use_container_width=True)

    with col_dir:
        st.markdown("""
        <div class="glass-card">
            <h3>📖 Dicionário das Variáveis</h3>
        </div>
        """, unsafe_allow_html=True)

        dicionario_df = pd.DataFrame({
            "Variável": list(DICIONARIO_VARIAVEIS.keys()),
            "Descrição": list(DICIONARIO_VARIAVEIS.values())
        })

        st.dataframe(dicionario_df, use_container_width=True)

    st.markdown("""
    <div class="glass-card">
        <h3>📊 Resumo Numérico</h3>
    </div>
    """, unsafe_allow_html=True)

    numericas = df.select_dtypes(include=["int64", "float64"]).columns

    if len(numericas) > 0:
        st.dataframe(df[numericas].describe(), use_container_width=True)
    else:
        st.info("Nenhuma variável numérica encontrada.")

    st.download_button(
        "⬇️ Baixar base tratada",
        data=df.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name="base_censo_escolar_ia.csv",
        mime="text/csv"
    )