"""
reader.py — Leitura e normalização da planilha Excel de setores censitários.
"""

import logging
from pathlib import Path

import pandas as pd

from .utils import clean_int_code, safe_str

# Colunas mínimas exigidas na planilha
REQUIRED_COLUMNS = [
    "CD_SETOR",    # código completo do setor (col A) — ex: "420140605000003P"
    "CD_MUN",      # código do município     (col G) — ex: 4201406
    "NM_MUN",      # nome do município       (col H)
    "CD_DIST",     # código do distrito      (col I) — ex: 420140605
    "NM_DIST",     # nome do distrito        (col J)
    "CD_SUBDIST",  # código do subdistrito   (col K) — ex: 42014060500 (pasta IBGE)
    "NM_SUBDIST",  # nome do subdistrito     (col L)
]

# Colunas cujo valor numérico precisa ser normalizado para string inteira
CODE_COLUMNS = ("CD_MUN", "CD_DIST", "CD_SUBDIST")


def read_spreadsheet(path: Path, logger: logging.Logger) -> pd.DataFrame:
    """
    Lê a planilha Excel e retorna DataFrame pronto para uso pelo scraper.

    Normalização aplicada:
      - Colunas de código (CD_*) → string inteira sem decimais
      - CD_SETOR                 → strip de espaços
      - Linhas sem CD_SETOR      → descartadas com aviso

    Returns:
        DataFrame com colunas de REQUIRED_COLUMNS, uma linha por setor.
    """
    logger.info(f"  Arquivo : {path.name}")

    # dtype=str força pandas a ler como texto, evitando conversão float
    df = pd.read_excel(
        path,
        dtype={c: str for c in REQUIRED_COLUMNS},
        engine="openpyxl",
    )

    # Valida colunas obrigatórias
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Colunas ausentes na planilha '{path.name}': {missing}\n"
            f"Colunas disponíveis: {list(df.columns)}"
        )

    df = df[REQUIRED_COLUMNS].copy()

    # Normaliza códigos numéricos
    for col in CODE_COLUMNS:
        df[col] = df[col].apply(clean_int_code)

    # Limpa campo de texto do setor
    df["CD_SETOR"] = df["CD_SETOR"].apply(lambda v: safe_str(v).strip())

    # Normaliza campos de nome (substitui NaN por string vazia)
    for col in ("NM_MUN", "NM_DIST", "NM_SUBDIST"):
        df[col] = df[col].apply(safe_str)

    # Descarta linhas sem setor válido
    before = len(df)
    df = df[df["CD_SETOR"].str.len() > 0].reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        logger.warning(f"  {dropped} linha(s) descartada(s) por CD_SETOR inválido/vazio")

    logger.info(
        f"  {len(df):,} setores  |  "
        f"{df['CD_MUN'].nunique()} municípios  |  "
        f"{df['CD_DIST'].nunique()} distritos  |  "
        f"{df['CD_SUBDIST'].nunique()} subdistritos"
    )
    return df
