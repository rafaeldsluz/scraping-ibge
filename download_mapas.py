"""
download_mapas.py
=================
Baixa todos os PDFs de mapas a partir da planilha de resultados já gerada.
Não refaz o scraping — usa as URLs da coluna MAPA_URL diretamente.

Funcionalidades:
  - Retomada automática: pula arquivos já baixados
  - Progresso em tempo real com ETA e velocidade média
  - Relatório final com total baixado, erros e tamanho em disco
  - Log dedicado ao download em logs/

Uso:
    py -3 download_mapas.py                        # usa o Excel mais recente
    py -3 download_mapas.py --xlsx output/meu.xlsx # especifica a planilha
    py -3 download_mapas.py --delay 0.1            # pausa entre downloads (s)
    py -3 download_mapas.py --workers 3            # downloads paralelos (máx 5)
"""

import argparse
import logging
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

BASE_DIR      = Path(__file__).parent
OUTPUT_DIR    = BASE_DIR / "output"
DOWNLOADS_DIR = BASE_DIR / "downloads" / "mapas"
LOGS_DIR      = BASE_DIR / "logs"

DEFAULT_DELAY   = 0.1   # pausa entre downloads (segundos)
DEFAULT_WORKERS = 1     # downloads simultâneos (1 = sequencial)
TIMEOUT         = 120   # segundos por arquivo


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_latest_excel() -> Path:
    """Retorna o arquivo resultado_mapas_*.xlsx mais recente em output/."""
    candidates = sorted(OUTPUT_DIR.glob("resultado_mapas_*.xlsx"))
    if not candidates:
        raise FileNotFoundError(
            f"Nenhum arquivo resultado_mapas_*.xlsx encontrado em {OUTPUT_DIR}.\n"
            "Execute primeiro: py -3 main.py"
        )
    return candidates[-1]


def _build_session(max_retries: int = 3) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=max_retries,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    session.headers["User-Agent"] = "IBGEMapScraper/1.0 (census-data-download)"
    return session


def _safe_filename(rec: dict) -> str:
    """Gera nome de arquivo: CD_MUN_CD_DIST_CD_SETOR.pdf"""
    raw = f"{rec['CD_MUN']}_{rec['CD_DIST']}_{rec['CD_SETOR']}.pdf"
    return "".join(c for c in raw if c.isalnum() or c in "._-")


def _download_one(
    rec: dict,
    dest_dir: Path,
    session: requests.Session,
    delay: float,
) -> dict:
    """
    Baixa um único PDF. Retorna dict com resultado da operação.
    Thread-safe: cada thread usa a mesma session (requests.Session é thread-safe
    para leituras concorrentes com conexões diferentes).
    """
    url      = rec["MAPA_URL"]
    filename = _safe_filename(rec)
    dest     = dest_dir / filename

    if dest.exists() and dest.stat().st_size > 0:
        return {"status": "skip", "filename": filename, "size_kb": dest.stat().st_size / 1024}

    time.sleep(delay)
    try:
        resp = session.get(url, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()

        tmp = dest.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=32_768):
                if chunk:
                    fh.write(chunk)

        tmp.rename(dest)  # atômico no mesmo volume
        size_kb = dest.stat().st_size / 1024
        return {"status": "ok", "filename": filename, "size_kb": size_kb}

    except Exception as exc:
        if dest.with_suffix(".tmp").exists():
            dest.with_suffix(".tmp").unlink(missing_ok=True)
        return {"status": "error", "filename": filename, "error": str(exc), "url": url}


# ── Main ──────────────────────────────────────────────────────────────────────

def main(xlsx_path: Path, delay: float, workers: int) -> int:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"download_{ts}.log"

    # Logger simples sem tqdm handler (download usa tqdm diretamente)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    log = logging.getLogger("download")

    # ── Leitura da planilha ──────────────────────────────────────────
    log.info(f"Planilha : {xlsx_path.name}")
    df = pd.read_excel(xlsx_path, sheet_name="Resultado", dtype=str, engine="openpyxl")

    pendentes = df[
        (df["STATUS"] == "ENCONTRADO") &
        (df["MAPA_URL"].str.startswith("http", na=False))
    ].to_dict("records")

    total       = len(pendentes)
    ja_baixados = sum(
        1 for r in pendentes
        if (DOWNLOADS_DIR / _safe_filename(r)).exists()
    )
    a_baixar = total - ja_baixados

    log.info(f"Total de mapas na planilha : {total:,}")
    log.info(f"Ja baixados (sera pulado)  : {ja_baixados:,}")
    log.info(f"A baixar agora             : {a_baixar:,}")
    log.info(f"Destino                    : {DOWNLOADS_DIR}")
    log.info(f"Workers                    : {workers}")
    log.info(f"Delay entre downloads      : {delay}s")
    log.info("-" * 60)

    if a_baixar == 0:
        log.info("Todos os arquivos ja foram baixados. Nada a fazer.")
        return 0

    session = _build_session()

    # ── Download ─────────────────────────────────────────────────────
    start      = time.perf_counter()
    ok = erros = skip = 0
    total_kb   = 0.0
    erros_log  = []

    with tqdm(
        total=total,
        initial=ja_baixados,
        desc="Baixando",
        unit="pdf",
        dynamic_ncols=True,
        unit_scale=False,
    ) as bar:
        if workers == 1:
            # Sequencial — mais simples e respeitoso com o servidor
            for rec in pendentes:
                result = _download_one(rec, DOWNLOADS_DIR, session, delay)
                _process_result(result, bar)
                if result["status"] == "ok":
                    ok += 1
                    total_kb += result["size_kb"]
                elif result["status"] == "error":
                    erros += 1
                    erros_log.append(result)
                else:
                    skip += 1
        else:
            # Paralelo limitado — use com cautela (max 5 workers)
            workers = min(workers, 5)
            lock = threading.Lock()

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_download_one, rec, DOWNLOADS_DIR, session, delay): rec
                    for rec in pendentes
                }
                for future in as_completed(futures):
                    result = future.result()
                    with lock:
                        _process_result(result, bar)
                        if result["status"] == "ok":
                            ok += 1
                            total_kb += result["size_kb"]
                        elif result["status"] == "error":
                            erros += 1
                            erros_log.append(result)
                        else:
                            skip += 1

    elapsed = time.perf_counter() - start

    # ── Relatório final ───────────────────────────────────────────────
    log.info("=" * 60)
    log.info(f"  Concluido em {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"  Baixados com sucesso : {ok:,}")
    log.info(f"  Ja existiam (pulados): {skip + ja_baixados:,}")
    log.info(f"  Erros                : {erros:,}")
    log.info(f"  Total baixado        : {total_kb/1024:.1f} MB  ({total_kb/1024/1024:.2f} GB)")
    log.info(f"  Pasta de destino     : {DOWNLOADS_DIR}")
    if erros_log:
        log.info(f"  Log de erros         : {log_file.name}")
        for e in erros_log[:10]:
            log.warning(f"  ERRO: {e['filename']} — {e['error']}")
    log.info("=" * 60)

    # Salva lista de erros para re-tentativa futura
    if erros_log:
        erros_path = LOGS_DIR / f"erros_download_{ts}.txt"
        with open(erros_path, "w", encoding="utf-8") as f:
            for e in erros_log:
                f.write(f"{e['url']}\n")
        log.info(f"  URLs com erro salvas em: {erros_path.name}")

    return 0


def _process_result(result: dict, bar: tqdm) -> None:
    """Atualiza a barra de progresso com informação do resultado."""
    if result["status"] in ("ok", "skip"):
        bar.update(1)
        if result["status"] == "ok":
            bar.set_postfix_str(
                f"{result['size_kb']:.0f}KB  {result['filename'][:25]}",
                refresh=False,
            )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Baixa PDFs de mapas censitários a partir da planilha de resultados."
    )
    parser.add_argument(
        "--xlsx",
        type=Path,
        default=None,
        help="Caminho da planilha resultado_mapas_*.xlsx (padrão: mais recente em output/)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Pausa entre downloads em segundos (padrão: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Downloads paralelos, max 5 (padrão: {DEFAULT_WORKERS})",
    )
    args = parser.parse_args()

    try:
        xlsx = args.xlsx or _find_latest_excel()
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}")
        sys.exit(1)

    sys.exit(main(xlsx_path=xlsx, delay=args.delay, workers=args.workers))
