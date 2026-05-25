# Scraping IBGE — Mapas de Setores Censitários SC

Automação em Python para localizar e baixar os mapas PDF de setores censitários do **Censo Demográfico 2022** do IBGE, a partir de uma planilha Excel com os códigos dos setores desejados.

O sistema navega o índice FTP-HTML do IBGE, resolve a URL de cada mapa (áreas urbanas **MSU** ou rurais **MSR**), exporta os resultados em planilha formatada com hiperlinks e, opcionalmente, faz o download dos PDFs.

---

## Funcionalidades

- Localiza automaticamente o PDF de mapa de cada setor censitário no índice do IBGE
- Cache em 4 camadas: reduz ~9.500 setores a ~450 requisições HTTP
- Fallback inteligente entre pastas de área (MSU → MSR)
- Exporta planilha Excel com hiperlinks clicáveis e 3 abas (Resultado, Sumário, Por Município)
- Download em lote dos PDFs com retomada automática (pula arquivos já baixados)
- Progresso em tempo real com ETA via `tqdm`
- Script auxiliar para filtrar setores por município e critério de prioridade (v0001)

---

## Estrutura do Projeto

```
scraping-ibge/
├── main.py                  # Ponto de entrada principal
├── download_mapas.py        # Download em lote a partir da planilha de resultados
├── requirements.txt
├── data/                    # Planilha de entrada (não versionada)
│   └── Setores_Censitarios_54municipios_SC.xlsx
├── output/                  # Planilhas de resultado geradas (não versionadas)
├── downloads/mapas/         # PDFs baixados (não versionados)
├── logs/                    # Logs de execução (não versionados)
└── src/
    ├── scraper.py           # IBGEScraper: navegação FTP-HTML + cache
    ├── reader.py            # Leitura da planilha Excel de entrada
    ├── exporter.py          # Exportação da planilha de resultados
    ├── downloader.py        # Download de PDFs
    └── utils.py             # Logger, RateLimiter, normalização
```

---

## Instalação

**Pré-requisitos:** Python 3.10+

```bash
# Clone o repositório
git clone https://github.com/rafaeldsluz/scraping-ibge.git
cd scraping-ibge

# Crie e ative um ambiente virtual
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# Instale as dependências
pip install -r requirements.txt
```

Coloque a planilha de entrada em `data/Setores_Censitarios_54municipios_SC.xlsx`.

---

## Uso

### Localizar URLs dos mapas

```bash
py -3 main.py
```

Gera `output/resultado_mapas_YYYYMMDD_HHMMSS.xlsx` com as URLs de todos os mapas encontrados.

### Localizar e baixar os PDFs

```bash
py -3 main.py --download
```

### Opções disponíveis

```
py -3 main.py --help

  --download    Baixar PDFs encontrados para downloads/mapas/
  --verbose     Exibir logs DEBUG no terminal
```

### Download em lote (a partir de resultado já gerado)

```bash
py -3 download_mapas.py                        # usa o Excel mais recente
py -3 download_mapas.py --xlsx output/meu.xlsx # especifica a planilha
py -3 download_mapas.py --workers 3            # downloads paralelos (máx 5)
py -3 download_mapas.py --delay 0.5            # pausa entre downloads (s)
```

---

## Planilha de Saída

A planilha `resultado_mapas_*.xlsx` contém 3 abas:

| Aba | Conteúdo |
|-----|----------|
| **Resultado** | Todos os setores com STATUS, MAPA_URL (hiperlink) e TIPO_AREA |
| **Sumário** | Totais por status (ENCONTRADO, SETOR NÃO ENCONTRADO, etc.) |
| **Por Município** | Contagem de mapas encontrados por município |

Possíveis valores de STATUS:

| Status | Descrição |
|--------|-----------|
| `ENCONTRADO` | URL do mapa localizada com sucesso |
| `SETOR NÃO ENCONTRADO` | Setor sem pasta no índice IBGE 2022 |
| `DISTRITO NÃO ENCONTRADO` | Subdistrito ausente no índice |
| `MUNICÍPIO NÃO ENCONTRADO` | Município não consta no índice |

---

## Como Funciona

O IBGE organiza os mapas no seguinte padrão de URL:

```
geoftp.ibge.gov.br/.../SC/
  └── {CD_MUN}/
        └── {CD_SUBDIST}/
              └── MSU/ ou MSR/
                    └── {num_setor}/
                          └── A3_{num_setor}_{MSU|MSR}.pdf
```

O scraper:
1. Carrega o índice de municípios (`load_municipalities`)
2. Pré-aquece o cache de subdistritos e tipos de área (`prewarm_cache`)
3. Resolve cada setor via lookups em dicionário (sem HTTP adicional)
4. Exporta a planilha com hiperlinks clicáveis

---

## Cobertura dos Dados

Testado com **9.598 setores** de **54 municípios** de Santa Catarina:

- ~89% dos setores localizados (`ENCONTRADO`)
- ~11% ausentes no índice IBGE 2022 (concentrados em Criciúma, Joinville e Florianópolis)
- ~450 requisições HTTP para cobrir todos os setores (graças ao cache)

---

## Dependências

| Pacote | Uso |
|--------|-----|
| `pandas` | Leitura e manipulação da planilha |
| `openpyxl` | Exportação Excel com hiperlinks |
| `requests` | Requisições HTTP ao índice IBGE |
| `beautifulsoup4` + `lxml` | Parse do HTML do índice FTP |
| `unidecode` | Normalização de nomes de municípios |
| `tqdm` | Barra de progresso com ETA |
| `urllib3` | Retry automático em falhas HTTP |

---

## Licença

MIT
