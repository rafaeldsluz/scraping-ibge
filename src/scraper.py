"""
scraper.py — Navegação no índice FTP-HTML do IBGE com cache agressivo.

Hierarquia de diretórios esperada:
    BASE_URL/
        {CD_MUN}/
            {CD_SUBDIST}/
                MSU/
                    {sector_num}/
                        A3_{sector_num}_MSU.pdf   ← mapa
                        d_{sector_num}.pdf        ← descritivo
                MSR/
                    {sector_num}/
                        A3_{sector_num}_MSR.pdf
                        d_{sector_num}.pdf

Estratégia de cache:
    - Índice de municípios    : 1 requisição (carregado uma vez)
    - Subdistritos por mun    : 1 requisição por município (≈54)
    - Tipos de área (MSU/MSR) : 1 requisição por subdistrito (≈136)
    - Setores por tipo de área : 1 requisição por (subdistrito × tipo) (≈272)
    Total estimado: ~463 requisições para 9.599 setores
"""

import logging
import re
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .utils import RateLimiter


class IBGEScraper:
    """
    Navega no índice FTP-HTML do IBGE para construir URLs de mapas.

    Todos os resultados de listagem de diretórios são armazenados em cache,
    garantindo que cada URL seja requisitada no máximo uma vez independente
    de quantos setores do mesmo município/distrito existam na planilha.
    """

    # Valores de status do resultado
    STATUS_FOUND     = "ENCONTRADO"
    STATUS_MUN_NF    = "MUNICÍPIO NÃO ENCONTRADO"
    STATUS_DIST_NF   = "DISTRITO NÃO ENCONTRADO"
    STATUS_SECTOR_NF = "SETOR NÃO ENCONTRADO"
    STATUS_PDF_NF    = "PDF NÃO ENCONTRADO"
    STATUS_HTTP_ERR  = "ERRO DE REQUISIÇÃO"

    def __init__(
        self,
        base_url:    str,
        delay:       float = 0.3,
        timeout:     int   = 30,
        max_retries: int   = 3,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout  = timeout
        self.logger   = logger or logging.getLogger(__name__)
        self._rate    = RateLimiter(delay)
        self.session  = self._build_session(max_retries)

        # ── Caches de listagem de diretórios ─────────────────────────
        # mun_index      : { cd_mun    → mun_url }
        # _subdist_cache : { mun_url   → { cd_subdist → subdist_url } }
        # _type_cache    : { subdist_url → { "MSU"|"MSR" → type_url } }
        # _sector_cache  : { type_url  → { sector_num → sector_url } }
        self.mun_index:      dict[str, str]             = {}
        self._subdist_cache: dict[str, dict[str, str]]  = {}
        self._type_cache:    dict[str, dict[str, str]]  = {}
        self._sector_cache:  dict[str, dict[str, str]]  = {}

        # Controle de log por município e distrito (evita repetição)
        self._seen_muns:  set[str]          = set()
        self._seen_dists: set[tuple[str, str]] = set()

    # ── Construção da sessão HTTP ─────────────────────────────────────

    def _build_session(self, max_retries: int) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=1.5,          # espera: 1.5s, 3s, 6s ...
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://",  adapter)
        session.headers["User-Agent"] = (
            "IBGEMapScraper/1.0 (census-data-research; python-requests)"
        )
        return session

    # ── Fetch e parse de listagem ─────────────────────────────────────

    def _fetch(self, url: str) -> Optional[str]:
        """GET com rate limiting e timeout. Retorna HTML ou None em falha."""
        self._rate.wait()
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            self.logger.debug(f"      GET {resp.status_code} {url}")
            return resp.text
        except requests.RequestException as exc:
            self.logger.warning(f"    [HTTP ERR] {url} — {exc}")
            return None

    def _parse_links(self, html: str, base_url: str) -> dict[str, str]:
        """
        Extrai links de um índice FTP-HTML retornando {nome: url_absoluta}.
        Ignora atalhos de navegação (?C=...), links para o topo (/) e externos.
        """
        soup   = BeautifulSoup(html, "lxml")
        result: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(("?", "/", "http", "ftp", "mailto")):
                continue
            name         = href.rstrip("/")
            result[name] = urljoin(base_url, href)
        return result

    # ── Carregadores com cache ────────────────────────────────────────

    def load_municipalities(self) -> None:
        """
        Carrega o índice de pastas de municípios da URL base.
        Deve ser chamado uma única vez antes de processar os setores.
        """
        self.logger.info(f"  Acessando: {self.base_url}")
        html = self._fetch(self.base_url)
        if not html:
            raise RuntimeError(
                "Falha ao acessar a URL base do IBGE. "
                "Verifique conexão e tente novamente."
            )
        self.mun_index = self._parse_links(html, self.base_url)

    def _load_subdists(self, mun_code: str, mun_url: str) -> dict[str, str]:
        """Carrega (e armazena em cache) os subdistritos de um município."""
        if mun_url not in self._subdist_cache:
            self.logger.debug(f"    Carregando subdistritos de {mun_code}…")
            html = self._fetch(mun_url)
            self._subdist_cache[mun_url] = (
                self._parse_links(html, mun_url) if html else {}
            )
            self.logger.debug(
                f"    → {len(self._subdist_cache[mun_url])} subdistritos"
            )
        return self._subdist_cache[mun_url]

    def _load_area_types(
        self, subdist_code: str, subdist_url: str
    ) -> dict[str, str]:
        """
        Carrega (e armazena em cache) quais tipos de área (MSU, MSR)
        existem dentro de um subdistrito.
        """
        if subdist_url not in self._type_cache:
            self.logger.debug(f"    Carregando tipos de área de {subdist_code}…")
            html  = self._fetch(subdist_url)
            links = self._parse_links(html, subdist_url) if html else {}
            # Filtra apenas as pastas relevantes
            self._type_cache[subdist_url] = {
                k: v for k, v in links.items() if k in ("MSU", "MSR")
            }
            self.logger.debug(
                f"    → tipos encontrados: {list(self._type_cache[subdist_url].keys())}"
            )
        return self._type_cache[subdist_url]

    def _load_sectors(self, area_type: str, type_url: str) -> dict[str, str]:
        """
        Carrega (e armazena em cache) os setores dentro de uma pasta
        MSU ou MSR. Retorna { sector_num: sector_url }.
        """
        if type_url not in self._sector_cache:
            self.logger.debug(f"    Carregando setores {area_type}: {type_url}")
            html = self._fetch(type_url)
            self._sector_cache[type_url] = (
                self._parse_links(html, type_url) if html else {}
            )
            self.logger.debug(
                f"    → {len(self._sector_cache[type_url])} setores em {area_type}"
            )
        return self._sector_cache[type_url]

    # ── Pré-aquecimento do cache ──────────────────────────────────────

    def prewarm_cache(self, df) -> None:
        """
        Pré-carrega todos os diretórios necessários antes do loop principal.

        Benefício: o tqdm do loop principal mostra ETA realista, pois as
        iterações de dict-lookup são quase instantâneas após o prewarm.

        Sequência de requisições:
          1 requisição  → municípios (já feita em load_municipalities)
          N requisições → subdistritos únicos (N = nº de municípios únicos)
          M requisições → tipos de área únicos  (M = nº de subdistritos únicos)
          K requisições → setores MSU/MSR únicos (K = M × 2)
        """
        import pandas as pd

        unique_pairs = (
            df[["CD_MUN", "NM_MUN", "CD_SUBDIST", "NM_DIST"]]
            .drop_duplicates()
            .reset_index(drop=True)
        )
        total_pairs = len(unique_pairs)
        self.logger.info(
            f"  Pre-aquecendo cache para {total_pairs} pares "
            f"(municipio x subdistrito)..."
        )

        from tqdm import tqdm as _tqdm
        for _, row in _tqdm(
            unique_pairs.iterrows(),
            total=total_pairs,
            desc="Cache (dirs)",
            unit="dir",
            dynamic_ncols=True,
        ):
            cd_mun     = str(row["CD_MUN"])
            cd_subdist = str(row["CD_SUBDIST"])

            mun_url = self.mun_index.get(cd_mun)
            if not mun_url:
                continue

            subdist_map = self._load_subdists(cd_mun, mun_url)
            subdist_url = subdist_map.get(cd_subdist)
            if not subdist_url:
                continue

            area_types = self._load_area_types(cd_subdist, subdist_url)
            for area_type, type_url in area_types.items():
                self._load_sectors(area_type, type_url)

        stats = self.cache_stats()
        self.logger.info(
            f"  Cache pronto: {stats['municipios_carregados']} munic. | "
            f"{stats['subdistritos_carregados']} subdist. | "
            f"{stats['total_requisicoes_http']} requisicoes HTTP realizadas"
        )

    # ── Resolução principal ───────────────────────────────────────────

    def resolve_sector(self, row) -> dict:
        """
        Dado um registro do DataFrame, percorre a hierarquia de diretórios
        do IBGE e retorna o dict com MAPA_URL e STATUS.

        Utiliza caches em todas as camadas: cada pasta é requisitada no
        máximo uma vez, independente de quantos setores compartilhem o
        mesmo município/subdistrito.

        Lógica de navegação:
            BASE_URL → {CD_MUN}/ → {CD_SUBDIST}/ → MSU|MSR/ → {sector_num}/
            → A3_{sector_num}_{tipo}.pdf
        """
        cd_mun     = str(row["CD_MUN"])
        nm_mun     = str(row["NM_MUN"])
        cd_dist    = str(row["CD_DIST"])
        nm_dist    = str(row["NM_DIST"])
        cd_subdist = str(row["CD_SUBDIST"])
        nm_subdist = str(row["NM_SUBDIST"])
        cd_setor   = str(row["CD_SETOR"]).strip()

        # Remove sufixo literal do código do setor para obter o nome da pasta
        # Ex.: "420140605000003P" → "420140605000003"
        sector_num = re.sub(r"[A-Za-z]+$", "", cd_setor)

        base_record = {
            "CD_SETOR":   cd_setor,
            "CD_MUN":     cd_mun,
            "NM_MUN":     nm_mun,
            "CD_DIST":    cd_dist,
            "NM_DIST":    nm_dist,
            "CD_SUBDIST": cd_subdist,
            "NM_SUBDIST": nm_subdist,
            "TIPO_AREA":  "",
            "MAPA_URL":   "",
            "STATUS":     "",
        }

        # Log ao encontrar município ou distrito pela primeira vez
        if cd_mun not in self._seen_muns:
            self._seen_muns.add(cd_mun)
            self.logger.info(f"[MUN] {cd_mun} — {nm_mun}")

        dist_key = (cd_mun, cd_subdist)
        if dist_key not in self._seen_dists:
            self._seen_dists.add(dist_key)
            self.logger.info(
                f"  [DIST] {cd_dist}  subdist={cd_subdist} — {nm_dist}"
            )

        # ── Etapa 1: localizar pasta do município ─────────────────────
        mun_url = self.mun_index.get(cd_mun)
        if not mun_url:
            self.logger.debug(
                f"    [NF-MUN] {cd_mun} ({nm_mun}) não encontrado no índice"
            )
            return {**base_record, "STATUS": self.STATUS_MUN_NF}

        # ── Etapa 2: localizar pasta do subdistrito ───────────────────
        subdist_map = self._load_subdists(cd_mun, mun_url)
        subdist_url = subdist_map.get(cd_subdist)
        if not subdist_url:
            self.logger.debug(
                f"    [NF-DIST] subdist={cd_subdist} não encontrado em {cd_mun}"
            )
            return {**base_record, "STATUS": self.STATUS_DIST_NF}

        # ── Etapa 3: identificar tipos de área disponíveis ────────────
        area_types = self._load_area_types(cd_subdist, subdist_url)
        if not area_types:
            self.logger.debug(
                f"    [NF-TYPE] nenhum MSU/MSR em subdist={cd_subdist}"
            )
            return {**base_record, "STATUS": self.STATUS_DIST_NF}

        # ── Etapa 4: procurar setor em MSU e depois MSR ───────────────
        for area_type in ("MSU", "MSR"):
            type_url = area_types.get(area_type)
            if not type_url:
                continue

            sectors = self._load_sectors(area_type, type_url)
            if sector_num not in sectors:
                continue

            sector_url   = sectors[sector_num]
            map_filename = f"A3_{sector_num}_{area_type}.pdf"
            map_url      = urljoin(sector_url, map_filename)

            self.logger.debug(
                f"    [OK] {cd_setor} → {area_type} → {map_url}"
            )
            return {
                **base_record,
                "TIPO_AREA": area_type,
                "MAPA_URL":  map_url,
                "STATUS":    self.STATUS_FOUND,
            }

        # Setor não encontrado em nenhuma área
        self.logger.debug(
            f"    [NF-SECTOR] {cd_setor} não encontrado em MSU/MSR "
            f"(subdist={cd_subdist})"
        )
        return {**base_record, "STATUS": self.STATUS_SECTOR_NF}

    # ── Estatísticas de cache ─────────────────────────────────────────

    def cache_stats(self) -> dict:
        """Retorna métricas do uso de cache para auditoria e debug."""
        return {
            "municipios_carregados": len(self._subdist_cache),
            "subdistritos_carregados": len(self._type_cache),
            "tipo_areas_carregadas": len(self._sector_cache),
            "total_requisicoes_http": (
                1  # load_municipalities
                + len(self._subdist_cache)
                + len(self._type_cache)
                + len(self._sector_cache)
            ),
        }
