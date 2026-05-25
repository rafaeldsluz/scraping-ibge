"""
IBGE Census Sector Map Scraper — main.py
=========================================
Localiza automaticamente URLs de mapas de setores censitários no índice
FTP-HTML do IBGE a partir de uma planilha Excel, com download opcional.

Uso:
    py -3 main.py                      # localiza URLs e gera planilha
    py -3 main.py --download           # localiza e baixa os PDFs
    py -3 main.py --verbose            # log DEBUG no terminal
    py -3 main.py --download --verbose # tudo
    py -3 main.py --help

Estrutura de saída:
    output/resultado_mapas_YYYYMMDD_HHMMSS.xlsx
    logs/ibge_scraper_YYYYMMDD_HHMMSS.log
    downloads/mapas/*.pdf              (apenas com --download)
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from src.downloader import download_pdfs
from src.exporter   import save_results
from src.reader     import read_spreadsheet
from src.scraper    import IBGEScraper
from src.utils      import setup_logger

# ── Configurações globais ─────────────────────────────────────────────────────

BASE_URL = (
    "https://geoftp.ibge.gov.br/cartas_e_mapas/"
    "mapas_para_fins_de_levantamentos_estatisticos/"
    "censo_demografico_2022/"
    "mapas_e_descritivos_de_setores_censitarios/SC/"
)

BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "data"
DOWNLOADS_DIR = BASE_DIR / "downloads" / "mapas"
LOGS_DIR      = BASE_DIR / "logs"
OUTPUT_DIR    = BASE_DIR / "output"

XLSX_FILENAME   = "setores_filtrados_20260524_215736.xlsx"
REQUEST_DELAY   = 0.3   # pausa mínima entre requisições (segundos)
REQUEST_TIMEOUT = 30    # timeout por requisição (segundos)
MAX_RETRIES     = 3     # tentativas em falha HTTP 5xx


# ── Funções auxiliares ────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    """Cria estrutura de pastas se não existir."""
    for d in (DATA_DIR, DOWNLOADS_DIR, LOGS_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _find_input() -> Path:
    """
    Procura a planilha em data/ e na raiz do projeto.
    Lança FileNotFoundError com mensagem orientativa se não encontrar.
    """
    for candidate in (DATA_DIR / XLSX_FILENAME, BASE_DIR / XLSX_FILENAME):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Planilha '{XLSX_FILENAME}' não encontrada.\n"
        f"  Coloque-a em: {DATA_DIR}  ou  {BASE_DIR}"
    )


def _print_header(logger, ts: str, download: bool, verbose: bool) -> None:
    sep = "=" * 68
    logger.info(sep)
    logger.info("  IBGE Census Sector Map Scraper  v1.0")
    logger.info(sep)
    logger.info(f"  Início      : {ts}")
    logger.info(f"  URL base    : {BASE_URL}")
    logger.info(f"  Download    : {'SIM' if download else 'NÃO'}")
    logger.info(f"  Verbose     : {'SIM' if verbose else 'NÃO'}")
    logger.info(sep)


# ── Função principal ──────────────────────────────────────────────────────────

def main(download: bool = False, verbose: bool = False) -> int:
    """
    Orquestra o pipeline completo:
      1. Leitura da planilha Excel
      2. Carregamento do índice de municípios IBGE
      3. Resolução de URL para cada setor (com cache)
      4. Exportação da planilha de resultados
      5. Download opcional dos PDFs

    Returns:
        Código de saída: 0 = sucesso, 1 = erro fatal
    """
    _ensure_dirs()

    ts       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts_file  = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR  / f"ibge_scraper_{ts_file}.log"
    out_file = OUTPUT_DIR / f"resultado_mapas_{ts_file}.xlsx"

    log = setup_logger("ibge_scraper", log_file, verbose=verbose)
    _print_header(log, ts, download, verbose)

    # ── 1. Leitura da planilha ────────────────────────────────────────
    try:
        input_path = _find_input()
    except FileNotFoundError as exc:
        log.error(str(exc))
        return 1

    log.info("[ 1/4 ] Lendo planilha Excel...")
    df = read_spreadsheet(input_path, log)

    # ── 2. Índice de municípios ───────────────────────────────────────
    scraper = IBGEScraper(
        base_url=BASE_URL,
        delay=REQUEST_DELAY,
        timeout=REQUEST_TIMEOUT,
        max_retries=MAX_RETRIES,
        logger=log,
    )

    log.info("[ 2/4 ] Carregando indice de municipios IBGE...")
    try:
        scraper.load_municipalities()
    except RuntimeError as exc:
        log.error(str(exc))
        return 1

    log.info(
        f"  {len(scraper.mun_index):,} pastas de municipios no indice"
    )

    # ── 3. Resolução setor a setor ────────────────────────────────────
    log.info("[ 3/4 ] Pre-aquecendo cache de diretorios IBGE...")
    scraper.prewarm_cache(df)

    log.info(f"  Processando {len(df):,} setores...")
    results: list[dict] = []
    for _, row in tqdm(
        df.iterrows(),
        total=len(df),
        desc="Setores",
        unit="setor",
        dynamic_ncols=True,
    ):
        results.append(scraper.resolve_sector(row))

    # Estatísticas de cache para auditoria
    stats = scraper.cache_stats()
    log.info(
        f"  Cache: {stats['municipios_carregados']} municípios | "
        f"{stats['subdistritos_carregados']} subdistritos | "
        f"{stats['total_requisicoes_http']} requisições HTTP no total"
    )

    # ── 4. Exportação ─────────────────────────────────────────────────
    log.info("[ 4/4 ] Exportando planilha de resultados...")
    save_results(results, out_file, log)

    # ── 5. Download (opcional) ────────────────────────────────────────
    if download:
        found = [r for r in results if r.get("MAPA_URL", "").startswith("http")]
        log.info(f"[DOWN] Download de {len(found):,} PDFs para downloads/mapas/...")
        download_pdfs(found, DOWNLOADS_DIR, scraper.session, log)

    # ── Resumo final ──────────────────────────────────────────────────
    found_count = sum(1 for r in results if r.get("STATUS") == "ENCONTRADO")
    total       = len(results)
    pct         = found_count / total * 100 if total else 0

    log.info("=" * 68)
    log.info(f"  CONCLUÍDO: {found_count:,}/{total:,} mapas localizados ({pct:.1f}%)")
    log.info(f"  Resultado : {out_file}")
    log.info(f"  Log       : {log_file}")
    log.info("=" * 68)

    print(f"\n{'='*55}")
    print(f"  Concluído: {found_count}/{total} mapas localizados ({pct:.1f}%)")
    print(f"  Resultado : {out_file.name}")
    print(f"  Pasta     : {out_file.parent}")
    print(f"{'='*55}")

    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="ibge-map-scraper",
        description=(
            "Localiza e opcionalmente baixa mapas de setores censitários IBGE "
            "a partir de planilha Excel."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Baixar PDFs encontrados para downloads/mapas/",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Exibir logs DEBUG no terminal (padrão: apenas INFO)",
    )

    args = parser.parse_args()
    sys.exit(main(download=args.download, verbose=args.verbose))
