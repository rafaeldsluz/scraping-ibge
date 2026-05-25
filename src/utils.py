"""
utils.py — Utilitários transversais: logging, rate limiting e normalização.
"""

import logging
import sys
import time
from pathlib import Path
from typing import Optional


# ── Logging ───────────────────────────────────────────────────────────────────

class _TqdmHandler(logging.Handler):
    """
    Handler que usa tqdm.write() para não corromper a barra de progresso
    quando logging e tqdm coexistem no mesmo terminal.
    """
    def emit(self, record: logging.LogRecord) -> None:
        try:
            from tqdm import tqdm
            msg = self.format(record)
            # Garante compatibilidade com terminais Windows (cp1252)
            msg = msg.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
                sys.stdout.encoding or "utf-8", errors="replace"
            )
            tqdm.write(msg, file=sys.stdout)
        except Exception:
            self.handleError(record)


def setup_logger(
    name:     str,
    log_file: Path,
    verbose:  bool = False,
) -> logging.Logger:
    """
    Cria logger com dois handlers:
      - Console  → INFO por padrão (DEBUG se verbose=True)
      - Arquivo  → DEBUG sempre (log completo para auditoria)
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Evita duplicar handlers em chamadas repetidas (ex.: testes)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_level = logging.DEBUG if verbose else logging.INFO
    ch = _TqdmHandler()
    ch.setLevel(console_level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ── Rate limiting ─────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Garante um intervalo mínimo entre chamadas consecutivas.
    Desconta o tempo já gasto na própria requisição HTTP, evitando
    sleep desnecessário quando o servidor já é lento.
    """

    def __init__(self, min_delay: float = 0.3) -> None:
        self._min_delay = min_delay
        self._last_call = 0.0

    def wait(self) -> None:
        elapsed = time.perf_counter() - self._last_call
        remaining = self._min_delay - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_call = time.perf_counter()


# ── Normalização de códigos ───────────────────────────────────────────────────

def clean_int_code(value) -> str:
    """
    Converte códigos numéricos do Excel para string inteira limpa.

    Lida com:
      - int   : 4201406       → "4201406"
      - float : 4201406.0     → "4201406"
      - str   : "4201406.0"   → "4201406"
      - NaN / None / ""       → ""
    """
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in ("", "nan", "none", "<na>"):
        return ""
    # Remove a parte decimal que pandas às vezes introduz
    if "." in s:
        s = s.rsplit(".", 1)[0]
    return s


def safe_str(value, default: str = "") -> str:
    """Converte qualquer valor para string, retornando default em NaN/None."""
    if value is None:
        return default
    s = str(value).strip()
    return default if s.lower() in ("nan", "none", "<na>") else s
