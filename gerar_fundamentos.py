"""
Gera o arquivo fundamentos_cvm.json a partir dos dados abertos da CVM.

Fontes (dados.cvm.gov.br):
  - DFP (Demonstrações Financeiras Padronizadas): balanço, DRE, DFC, DVA e
    composição do capital das companhias abertas.
  - FCA (Formulário Cadastral): códigos de negociação (tickers) de cada companhia.

Uso:
  python gerar_fundamentos.py                  -> baixa os arquivos mais recentes da CVM
  python gerar_fundamentos.py dfp.zip fca.zip  -> usa arquivos já baixados

O exercício analisado (t) é o "ÚLTIMO" do arquivo DFP; o exercício anterior (t-1)
é o "PENÚLTIMO" do mesmo arquivo. Valores em reais (a escala MIL é convertida).
"""

import csv
import io
import json
import re
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

URL_DFP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
URL_FCA = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{ano}.zip"
MIN_EMPRESAS = 300  # um arquivo DFP com menos empresas é considerado incompleto (ano em andamento)


# ----------------------------------------------------------------------------- download
def baixar(url):
    print("Baixando", url, flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (TCC agente)"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read(), r.headers.get("Last-Modified", "")


def abrir_zip_mais_recente(modelo, verificar=None):
    """Tenta do ano atual para trás e devolve (ano, ZipFile, data_modificacao)."""
    ano = datetime.now().year
    for a in range(ano, ano - 4, -1):
        try:
            dados, modificado = baixar(modelo.format(ano=a))
        except Exception as e:  # noqa: BLE001
            print("  indisponível:", e)
            continue
        z = zipfile.ZipFile(io.BytesIO(dados))
        if verificar and not verificar(z, a):
            print("  arquivo de", a, "incompleto; tentando o ano anterior")
            continue
        return a, z, modificado
    raise SystemExit("Nenhum arquivo encontrado em " + modelo)


# ----------------------------------------------------------------------------- leitura
def linhas(z, nome):
    if nome not in z.namelist():
        return
    with z.open(nome) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def nome_arquivo(z, parte):
    for n in z.namelist():
        if parte in n:
            return n
    return None


def contar_empresas_dfp(z, ano):
    n = nome_arquivo(z, f"dfp_cia_aberta_{ano}.csv")
    return n is not None and len({r["CNPJ_CIA"] for r in linhas(z, n)}) >= MIN_EMPRESAS


def ler_demonstracoes(z, tipo, sufixo):
    """Devolve {cnpj: {ordem: {cd_conta: (descricao, valor_em_reais)}}} e dados cadastrais."""
    nome = nome_arquivo(z, f"_{tipo}_{sufixo}_")
    dados, cad = {}, {}
    if not nome:
        return dados, cad
    for r in linhas(z, nome):
        escala = 1000.0 if r.get("ESCALA_MOEDA", "").upper() == "MIL" else 1.0
        if r["CD_CONTA"].startswith("3.99"):  # lucro por ação já vem em reais por ação
            escala = 1.0
        v = num(r["VL_CONTA"])
        ordem = "t" if r["ORDEM_EXERC"].startswith("Ú") or r["ORDEM_EXERC"].startswith("U") else "t1"
        dados.setdefault(r["CNPJ_CIA"], {}).setdefault(ordem, {})[r["CD_CONTA"]] = (
            r["DS_CONTA"], None if v is None else v * escala)
        cad[r["CNPJ_CIA"]] = {"nome": r["DENOM_CIA"], "cd_cvm": r["CD_CVM"],
                              "fim": r.get("DT_FIM_EXERC", "")}
    return dados, cad


# ----------------------------------------------------------------------------- contas
def valor(contas, codigo):
    item = contas.get(codigo)
    return item[1] if item else None


def valor_por_descricao(contas, prefixo_codigo, padrao):
    """Procura, entre as contas de nível 2 (ex.: 3.11), a primeira cuja descrição casa com o padrão."""
    for cd, (ds, v) in sorted(contas.items(), key=lambda x: [int(p) for p in x[0].split(".")]):
        if cd.startswith(prefixo_codigo) and cd.count(".") == 1 and re.search(padrao, ds, re.I):
            return v
    return None


def extrair(bpa, bpp, dre, dfc, dva):
    """Contas usadas pela calculadora (mesmos nomes do bloco 'Calcular indicadores')."""
    ebit = valor_por_descricao(dre, "3.", r"^Resultado Antes do Resultado Financeiro")
    deprec = valor(dva, "7.04.01")
    return {
        "receita": valor(dre, "3.01"),
        "lucroBruto": valor_por_descricao(dre, "3.", r"^Resultado Bruto"),
        "ebit": ebit,
        "depreciacaoAmortizacao": deprec,
        "ebitda": (ebit - deprec) if ebit is not None and deprec is not None else None,
        "lucroLiquido": valor_por_descricao(dre, "3.", r"^Lucro.{0,3}Preju"),
        "ativoTotal": valor(bpa, "1"),
        "ativoCirculante": valor(bpa, "1.01"),
        "caixaEquivalentes": valor(bpa, "1.01.01"),
        "aplicacoesFinanceiras": valor(bpa, "1.01.02"),
        "passivoCirculante": valor(bpp, "2.01"),
        "passivoNaoCirculante": valor(bpp, "2.02"),
        "emprestimosCP": valor(bpp, "2.01.04"),
        "emprestimosLP": valor(bpp, "2.02.01"),
        # empréstimos, financiamentos e debêntures registrados fora das contas padrão
        "emprestimosOutrosCP": _dividas_fora_padrao(bpp, "2.01.", "2.01.04"),
        "emprestimosOutrosLP": _dividas_fora_padrao(bpp, "2.02.", "2.02.01"),
        "patrimonioLiquido": valor_por_descricao(bpp, "2.", r"^Patrim.nio L.quido"),
        "capitalSocial": _capital(bpp),
        "fco": valor(dfc, "6.01"),
        "_lucroControladores": valor(dre, "3.11.01") or valor(dre, "3.09.01"),
        "_lpaBasico": max([abs(v) for cd, (ds, v) in dre.items()
                           if cd.startswith("3.99.01.") and v] or [0]) or None,
    }


# Contas cujo nome começa por empréstimo/financiamento/debênture; excluem-se itens que não são
# dívida financeira tradicional (debêntures participativas, ajustes a valor justo, empréstimo
# compulsório, refinanciamento fiscal e acordos com fornecedores).
PADRAO_DIVIDA = re.compile(r"^\s*(empr[eé]stimos?|financiamentos?|deb[eê]ntures?|credores por financiamento)", re.I)
EXCLUIR_DIVIDA = re.compile(r"compuls|participativ|ajuste|fiscal|fornecedor", re.I)


def _dividas_fora_padrao(bpp, grupo, conta_padrao):
    """Soma subcontas de empréstimos/financiamentos/debêntures que ficam fora da conta padrão
    (ex.: dentro de 'Outras Obrigações'). Para não contar em dobro, usa só a conta mais alta
    da hierarquia que casa com o padrão."""
    candidatos = {cd: v for cd, (ds, v) in bpp.items()
                  if cd.startswith(grupo) and not cd.startswith(conta_padrao)
                  and PADRAO_DIVIDA.search(ds or "") and not EXCLUIR_DIVIDA.search(ds or "") and v}
    total = 0.0
    for cd, v in candidatos.items():
        partes = cd.split(".")
        ancestrais = [".".join(partes[:i]) for i in range(2, len(partes))]
        if not any(a in candidatos for a in ancestrais):
            total += v
    return total or None


def _capital(bpp):
    for cd, (ds, v) in bpp.items():
        if cd.count(".") == 2 and cd.startswith("2.0") and re.search(r"^Capital Social", ds, re.I):
            return v
    return None


def tipo_estrutura(bpa):
    ds = (bpa.get("1.01") or ("", None))[0]
    return "padrao" if re.search(r"Ativo Circulante", ds, re.I) else "financeira"


# ----------------------------------------------------------------------------- tickers (FCA)
def ler_tickers(z):
    nome = nome_arquivo(z, "valor_mobiliario")
    mapa = {}
    if not nome:
        return mapa
    for r in linhas(z, nome):
        cod = (r.get("Codigo_Negociacao") or "").strip().upper()
        cnpj = (r.get("CNPJ_Companhia") or "").strip()
        mercado = (r.get("Mercado") or "").lower()
        if not cod or not cnpj or not re.fullmatch(r"[A-Z]{4}\d{1,2}", cod):
            continue
        if mercado and "bolsa" not in mercado:
            continue
        if (r.get("Data_Fim_Negociacao") or "").strip():
            continue
        mapa[cod] = cnpj
    return mapa


def ler_setores(z):
    nome = nome_arquivo(z, "_geral_")
    return {r["CNPJ_Companhia"]: (r.get("Setor_Atividade") or "").strip()
            for r in linhas(z, nome)} if nome else {}


# ----------------------------------------------------------------------------- principal
def gerar(z_dfp, ano_dfp, mod_dfp, z_fca, ano_fca, mod_fca, z_fca_anterior=None):
    dem = {}
    for sufixo in ("con", "ind"):
        d = {}
        for tipo in ("BPA", "BPP", "DRE", "DFC_MI", "DFC_MD", "DVA"):
            d[tipo], cad = ler_demonstracoes(z_dfp, tipo, sufixo)
            if tipo == "BPA":
                d["cad"] = cad
        dem[sufixo] = d

    def preenchida(d, cnpj):
        """Demonstração utilizável: ativo total e receita/resultado diferentes de zero no exercício t."""
        bpa = d["BPA"].get(cnpj, {}).get("t", {})
        dre = d["DRE"].get(cnpj, {}).get("t", {})
        return bool(valor(bpa, "1")) and any(v for cd, (ds, v) in dre.items() if cd.count(".") == 1)

    empresas, lucro_ctrl, lpa = {}, {}, {}
    cnpjs = set(dem["con"]["BPA"]) | set(dem["ind"]["BPA"])
    for cnpj in cnpjs:
        # lucro atribuído aos controladores e lucro por ação básico (para conferir a unidade das ações)
        for sufixo in ("ind", "con"):
            dre_t = dem[sufixo]["DRE"].get(cnpj, {}).get("t", {})
            l = valor_por_descricao(dre_t, "3.", r"^Lucro.{0,3}Preju") if sufixo == "ind" else valor(dre_t, "3.11.01")
            p = max([abs(v) for cd, (ds, v) in dre_t.items() if cd.startswith("3.99.01.") and v] or [0])
            if l and p and cnpj not in lpa:
                lucro_ctrl[cnpj], lpa[cnpj] = l, p

        # consolidado tem prioridade, desde que esteja preenchido
        sufixo = "con" if preenchida(dem["con"], cnpj) else ("ind" if preenchida(dem["ind"], cnpj) else None)
        if not sufixo:
            continue
        d = dem[sufixo]
        cad = d["cad"][cnpj]
        reg = {"nome": cad["nome"], "cd_cvm": cad["cd_cvm"],
               "demonstracao": "consolidada" if sufixo == "con" else "individual",
               "estrutura": tipo_estrutura(d["BPA"][cnpj]["t"]),
               "exercicio_t": cad["fim"][:4]}
        for ordem in ("t", "t1"):
            g = lambda tipo: d[tipo].get(cnpj, {}).get(ordem, {})  # noqa: E731
            reg["contas_" + ordem] = extrair(g("BPA"), g("BPP"), g("DRE"), g("DFC_MI") or g("DFC_MD"), g("DVA"))
            reg["contas_" + ordem].pop("_lucroControladores", None)
            reg["contas_" + ordem].pop("_lpaBasico", None)
        ct = reg["contas_t"]
        if reg["estrutura"] == "padrao" and not ct.get("receita"):
            reg["estrutura"] = "sem_receita"  # holdings/participações: sem receita operacional
        divida = sum(abs(ct.get(k) or 0) for k in ("emprestimosCP", "emprestimosLP", "emprestimosOutrosCP", "emprestimosOutrosLP"))
        reg["divida_identificada"] = divida > 0
        empresas[cnpj] = reg

    for r in linhas(z_dfp, nome_arquivo(z_dfp, "composicao_capital")):
        e = empresas.get(r["CNPJ_CIA"])
        if not e:
            continue
        qtd = (num(r["QT_ACAO_TOTAL_CAP_INTEGR"]) or 0) - (num(r["QT_ACAO_TOTAL_TESOURO"]) or 0)
        # Algumas companhias informam a quantidade de ações em milhares (sem indicar a unidade).
        # Conferência: ações implícitas = lucro dos controladores / lucro por ação básico.
        l, p = lucro_ctrl.get(r["CNPJ_CIA"]), lpa.get(r["CNPJ_CIA"])
        e["acoes_ajuste"] = "nenhum"
        if qtd and l and p:
            razao = (abs(l) / p) / qtd
            if 300 <= razao <= 3000:
                qtd *= 1000
                e["acoes_ajuste"] = "multiplicado por 1000 (informado em milhares)"
            elif not 0.3 <= razao <= 3:
                e["acoes_ajuste"] = "verificar: quantidade de ações inconsistente com o lucro por ação"
        elif qtd:
            e["acoes_ajuste"] = "não verificado (sem lucro por ação)"
        e["acoes_em_circulacao"] = qtd or None

    setores = ler_setores(z_fca_anterior) if z_fca_anterior else {}
    setores.update(ler_setores(z_fca))
    for cnpj, setor in setores.items():
        if cnpj in empresas and setor:
            empresas[cnpj]["setor"] = setor

    # tickers: o FCA mais recente prevalece; o do ano anterior cobre quem ainda não entregou o atual
    todos = ler_tickers(z_fca_anterior) if z_fca_anterior else {}
    atuais = ler_tickers(z_fca)
    cnpjs_atuais = set(atuais.values())
    todos = {t: c for t, c in todos.items() if c not in cnpjs_atuais}
    todos.update(atuais)
    tickers = {t: c for t, c in todos.items() if c in empresas}
    return {
        "gerado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fonte": {
            "dfp": f"CVM - DFP {ano_dfp} (dfp_cia_aberta_{ano_dfp}.zip), modificado em {mod_dfp}".strip(),
            "fca": f"CVM - FCA {ano_fca} (fca_cia_aberta_{ano_fca}.zip), modificado em {mod_fca}".strip(),
            "unidade": "reais",
        },
        "tickers": dict(sorted(tickers.items())),
        "empresas": dict(sorted(empresas.items())),
    }


def main():
    if len(sys.argv) >= 3:
        z_dfp, z_fca = zipfile.ZipFile(sys.argv[1]), zipfile.ZipFile(sys.argv[2])
        a_dfp = int(re.search(r"(\d{4})", " ".join(z_dfp.namelist())).group(1))
        a_fca = int(re.search(r"(\d{4})", " ".join(z_fca.namelist())).group(1))
        m_dfp = m_fca = "arquivo local"
    else:
        a_dfp, z_dfp, m_dfp = abrir_zip_mais_recente(URL_DFP, contar_empresas_dfp)
        a_fca, z_fca, m_fca = abrir_zip_mais_recente(URL_FCA)
    z_fca_ant = None
    if len(sys.argv) < 3:
        try:
            z_fca_ant = zipfile.ZipFile(io.BytesIO(baixar(URL_FCA.format(ano=a_fca - 1))[0]))
        except Exception as e:  # noqa: BLE001
            print("  FCA do ano anterior indisponível:", e)
    saida = gerar(z_dfp, a_dfp, m_dfp, z_fca, a_fca, m_fca, z_fca_ant)
    with open("fundamentos_cvm.json", "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK: {len(saida['empresas'])} empresas, {len(saida['tickers'])} tickers")


if __name__ == "__main__":
    main()
