"""
downloader.py — Download automático de PDFs de mapas censitários.

Nomenclatura de arquivo: {CD_MUN}_{CD_DIST}_{CD_SETOR}.pdf
Destino padrão         : downloads/mapas/

Funcionalidades:
  - Skip automático se o arquivo já existir
  - Download em streaming (sem carregar o PDF inteiro na memória)
  - Remoção de arquivo incompleto em caso de falha
  - Relatório final de sucesso/erro/ignorado
"""

import logging
import time
from pathlib import Path

import requests
from tqdm import tqdm


def download_pdfs(
    records:  list[dict],
    dest_dir: Path,
    session:  requests.Session,
    logger:   logging.Logger,
    delay:    float = 0.5,
    timeout:  int   = 120,
) -> dict[str, int]:
    """
    Baixa os PDFs cujas URLs estão em records['MAPA_URL'].

    Args:
        records:  lista de dicts com chaves MAPA_URL, CD_MUN, CD_DIST, CD_SETOR
        dest_dir: pasta de destino (criada automaticamente se não existir)
        session:  sessão HTTP reutilizável (com retry configurado)
        logger:   logger do projeto
        delay:    pausa mínima entre downloads (segundos)
        timeout:  timeout por arquivo (segundos)

    Returns:
        dict com contagens: {"success": N, "errors": N, "skipped": N}
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    success = errors = skipped = 0

    for rec in tqdm(records, desc="Baixando PDFs", unit="pdf", leave=True):
        url = rec.get("MAPA_URL", "")
        if not url.startswith("http"):
            continue

        filename = _build_filename(rec)
        dest     = dest_dir / filename

        if dest.exists():
            logger.debug(f"  [SKIP] {filename} — já existe")
            skipped += 1
            continue

        try:
            time.sleep(delay)
            resp = session.get(url, timeout=timeout, stream=True)
            resp.raise_for_status()

            # Download em chunks para não acumular em memória
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=16_384):
                    if chunk:
                        fh.write(chunk)

            size_kb = dest.stat().st_size / 1024
            logger.debug(f"  [OK] {filename}  ({size_kb:.1f} KB)")
            success += 1

        except requests.RequestException as exc:
            logger.warning(f"  [ERRO] {url} — {exc}")
            # Remove arquivo parcial para não deixar lixo no disco
            if dest.exists():
                dest.unlink()
            errors += 1

    report = {"success": success, "errors": errors, "skipped": skipped}
    logger.info(
        f"  Downloads: {success} concluídos | "
        f"{errors} erros | "
        f"{skipped} já existentes"
    )
    return report


def _build_filename(rec: dict) -> str:
    """
    Gera nome de arquivo padronizado e seguro para o sistema de arquivos.
    Formato: {CD_MUN}_{CD_DIST}_{CD_SETOR}.pdf
    """
    raw = f"{rec.get('CD_MUN', 'X')}_{rec.get('CD_DIST', 'X')}_{rec.get('CD_SETOR', 'X')}.pdf"
    # Mantém apenas caracteres seguros para nomes de arquivo
    safe = "".join(c for c in raw if c.isalnum() or c in "._-")
    return safe
