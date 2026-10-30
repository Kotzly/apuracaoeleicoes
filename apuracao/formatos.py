"""Formatos de arquivo do TSE e o mapeamento para o schema canônico.

A pipeline é uma só; o que muda entre anos fica isolado num `Formato`:
  - onde achar os arquivos (conjunto no portal, padrão do nome);
  - como ler o arquivo bruto (cabeçalho, separador, codificação);
  - de que coluna bruta sai cada coluna canônica, e em que formato vêm as datas.

Para um ano novo com layout diferente, crie uma subclasse que sobrescreve só
o que mudou e registre-a com @registrar(ano). Anos sem formato registrado usam
o formato do ano registrado mais recente anterior a eles.

Layouts conferidos em arquivos reais (AC, 1º turno):
  2016  TXT sem cabeçalho, 31 colunas posicionais (leia-meArquivo_BU-WEB.pdf),
        sem turno nem descrição do tipo de votável.
  2018  CSV com cabeçalho; coluna "SG_ UF" (com espaço); SEM DT_BU_RECEBIDO e
        SEM DT_EMISSAO_BU -> não há como reconstruir a evolução da apuração.
  2020  CSV igual ao de 2022; tem aspas mal escapadas em alguns nomes.
  2024  CSV com números sem aspas e datas ISO (AAAA-MM-DD hh:mm:ss).

Fusos: abertura/encerramento/emissão vêm sempre em horário local da urna.
O recebimento vem em horário de Brasília em 2020 e 2024 (e, presume-se, 2022),
mas em horário local em 2016. Tudo é gravado em horário de Brasília.
"""

from __future__ import annotations

import re
from pathlib import Path

# Schema canônico: coluna -> tipo. Toda carga produz exatamente estas colunas;
# o que o arquivo de um ano não tiver vira NULL.
CANONICO: dict[str, str] = {
    "ano": "SMALLINT",
    "nr_turno": "TINYINT",
    "cd_eleicao": "INTEGER",
    "sg_uf": "VARCHAR",            # 'ZZ' = exterior
    "cd_municipio": "INTEGER",
    "nm_municipio": "VARCHAR",
    "nr_zona": "INTEGER",          # zona é única só dentro da UF: use (sg_uf, nr_zona)
    "nr_secao": "INTEGER",
    "cd_cargo": "SMALLINT",
    "ds_cargo": "VARCHAR",
    "ds_tipo_votavel": "VARCHAR",  # Nominal, Legenda, Branco, Nulo
    "nr_votavel": "INTEGER",
    "nm_votavel": "VARCHAR",
    "sg_partido": "VARCHAR",
    "qt_votos": "INTEGER",
    "qt_aptos": "INTEGER",
    "qt_comparecimento": "INTEGER",
    "qt_abstencoes": "INTEGER",
    "ts_abertura": "TIMESTAMP",
    "ts_encerramento": "TIMESTAMP",
    "ts_emissao_bu": "TIMESTAMP",
    "ts_bu_recebido": "TIMESTAMP",
}

# Fuso de cada UF, para levar horários locais da urna ao horário de Brasília.
# Simplificação: usa o fuso da capital (ex.: oeste do AM e Fernando de Noronha
# ficam com o fuso principal da UF). 'ZZ' (exterior) não é convertido.
FUSO_UF = {
    "AC": "America/Rio_Branco", "AM": "America/Manaus", "RR": "America/Boa_Vista",
    "RO": "America/Porto_Velho", "MT": "America/Cuiaba", "MS": "America/Campo_Grande",
    "PA": "America/Belem", "AP": "America/Belem", "TO": "America/Araguaina",
    "MA": "America/Fortaleza", "PI": "America/Fortaleza", "CE": "America/Fortaleza",
    "RN": "America/Fortaleza", "PB": "America/Fortaleza", "PE": "America/Recife",
    "AL": "America/Maceio", "SE": "America/Maceio", "BA": "America/Bahia",
    "MG": "America/Sao_Paulo", "ES": "America/Sao_Paulo", "RJ": "America/Sao_Paulo",
    "SP": "America/Sao_Paulo", "PR": "America/Sao_Paulo", "SC": "America/Sao_Paulo",
    "RS": "America/Sao_Paulo", "GO": "America/Sao_Paulo", "DF": "America/Sao_Paulo",
}
FUSO_BRASILIA = "America/Sao_Paulo"

# Marcadores de valor ausente usados pelo TSE.
NULOS = ("", "#NULO#", "#NE#")

_REGISTRO: dict[int, type[Formato]] = {}


def registrar(*anos: int):
    def deco(cls):
        for a in anos:
            _REGISTRO[a] = cls
        return cls
    return deco


def formato_para(ano: int) -> Formato:
    if ano in _REGISTRO:
        return _REGISTRO[ano](ano)
    anteriores = [a for a in _REGISTRO if a < ano]
    if not anteriores:
        raise SystemExit(f"Nenhum formato registrado para {ano} ou antes. "
                         f"Anos suportados: {sorted(_REGISTRO)}")
    base = max(anteriores)
    print(f"aviso: {ano} sem formato próprio; usando o de {base}. "
          "Rode `python -m apuracao inspecionar` num arquivo para conferir.")
    return _REGISTRO[base](ano)


class Formato:
    """Base: arquivos de texto ';' dentro de zips, um por UF e turno, no portal CKAN do TSE."""

    # Descoberta
    pacote = "resultados-{ano}-boletim-de-urna"
    padrao_arquivo = re.compile(r"bweb_(?P<turno>\d)t_(?P<uf>[A-Z]{2})_\d+\.zip$", re.I)
    extensao_dados = ".csv"

    # Leitura
    delim = ";"
    encoding = "latin-1"
    nomes: list[str] | None = None   # para arquivos sem cabeçalho: nome de cada posição

    # Mapeamento: coluna canônica -> colunas brutas candidatas (a primeira presente vence)
    mapa: dict[str, list[str]] = {}
    # Sem estas a carga falha; as demais viram NULL se ausentes.
    obrigatorias = ("nr_turno", "sg_uf", "cd_municipio", "nr_zona", "nr_secao",
                    "cd_cargo", "nr_votavel", "qt_votos")
    formatos_data = ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")
    # Colunas canônicas que o arquivo traz em horário local da urna. Na carga elas
    # são convertidas para o horário de Brasília, para que a curva nacional use um
    # relógio só. As demais já vêm em horário de Brasília (horário do servidor).
    horario_local: tuple[str, ...] = ("ts_abertura", "ts_encerramento", "ts_emissao_bu")

    def __init__(self, ano: int):
        self.ano = ano

    def nome_pacote(self) -> str:
        return self.pacote.format(ano=self.ano)

    def fonte_sql(self, arquivos: list[Path]) -> str:
        lista = ", ".join(f"'{a.as_posix()}'" for a in arquivos)
        # strict_mode=false: o TSE às vezes publica aspas internas sem escape
        # (ex.: ""APELIDO""); o modo estrito rejeitaria a linha inteira.
        opcoes = (f"delim='{self.delim}', quote='\"', escape='\"', all_varchar=true, "
                  f"encoding='{self.encoding}', strict_mode=false")
        if self.nomes:
            nomes = ", ".join(f"'{n}'" for n in self.nomes)
            opcoes += f", header=false, names=[{nomes}]"
        else:
            opcoes += ", header=true, union_by_name=true"
        return f"read_csv([{lista}], {opcoes})"

    def linhas_de_cabecalho(self) -> int:
        return 0 if self.nomes else 1

    def constantes(self, arquivos: list[Path]) -> dict[str, str]:
        """Valores para colunas canônicas ausentes do arquivo, tirados do contexto."""
        const = {"ano": str(self.ano)}
        m = self.padrao_arquivo.search(arquivos[0].name.rsplit(".", 1)[0] + ".zip")
        if m:
            const["nr_turno"] = m["turno"]
        return const

    def resolver(self, existentes: set[str]) -> dict[str, str | None]:
        """Coluna canônica -> coluna bruta usada (ou None se ausente)."""
        return {c: next((b for b in self.mapa.get(c, []) if b in existentes), None)
                for c in CANONICO}

    @staticmethod
    def _q(coluna: str) -> str:
        return '"' + coluna.replace('"', '""') + '"'

    def expressao(self, canonica: str, bruta: str | None, constante: str | None) -> str:
        """Expressão SQL que produz a coluna canônica a partir da bruta."""
        tipo = CANONICO[canonica]
        if bruta is None:
            return f"'{constante}'::{tipo}" if constante is not None else f"NULL::{tipo}"
        nulos = ", ".join(f"'{n}'" for n in NULOS)
        valor = f"CASE WHEN trim({self._q(bruta)}) IN ({nulos}) THEN NULL " \
                f"ELSE trim({self._q(bruta)}) END"
        if tipo == "TIMESTAMP":
            tentativas = ", ".join(f"try_strptime({valor}, '{f}')" for f in self.formatos_data)
            return f"coalesce({tentativas})"
        if tipo == "VARCHAR":
            return valor
        expr = f"TRY_CAST({valor} AS {tipo})"
        return f"coalesce({expr}, '{constante}'::{tipo})" if constante is not None else expr

    def select_sql(self, fonte: str, existentes: set[str], arquivos: list[Path],
                   cargos: list[int] | None = None) -> tuple[str, list[str]]:
        """SELECT no schema canônico e a lista de colunas canônicas que ficaram NULL."""
        res = self.resolver(existentes)
        const = self.constantes(arquivos)
        faltando = [c for c in self.obrigatorias if res[c] is None and c not in const]
        if faltando:
            raise ValueError(
                f"{self.ano}: colunas obrigatórias sem correspondência no arquivo: {faltando}. "
                "Rode `python -m apuracao inspecionar` e ajuste o Formato deste ano.")
        cols = ",\n  ".join(f"{self.expressao(c, b, const.get(c))} AS {c}"
                            for c, b in res.items())
        filtro = ""
        if cargos:
            filtro = (f"WHERE TRY_CAST({self._q(res['cd_cargo'])} AS INTEGER) IN "
                      f"({','.join(map(str, cargos))})")
        nulas = [c for c, b in res.items() if b is None and c not in const]
        sql = f"SELECT\n  {cols}\nFROM {fonte}\n{filtro}"
        locais = [c for c in self.horario_local if res[c] is not None]
        if locais:
            fuso = "CASE sg_uf " + " ".join(f"WHEN '{uf}' THEN '{tz}'"
                                            for uf, tz in FUSO_UF.items()) + " END"
            troca = ", ".join(
                f"CASE WHEN {fuso} IS NULL THEN {c} ELSE "
                f"timezone('{FUSO_BRASILIA}', timezone({fuso}, {c})) END AS {c}"
                for c in locais)
            sql = f"SELECT * REPLACE ({troca}) FROM (\n{sql}\n)"
        return sql, nulas


@registrar(2018, 2020, 2022, 2024, 2026)
class BwebCSV(Formato):
    """Boletim de urna na web (bweb), CSV com cabeçalho, publicado desde 2018."""

    mapa = {
        "ano": ["ANO_ELEICAO"],
        "nr_turno": ["NR_TURNO"],
        "cd_eleicao": ["CD_ELEICAO"],
        "sg_uf": ["SG_UF", "SG_ UF"],            # 2018 tem um espaço no nome
        "cd_municipio": ["CD_MUNICIPIO"],
        "nm_municipio": ["NM_MUNICIPIO"],
        "nr_zona": ["NR_ZONA"],
        "nr_secao": ["NR_SECAO"],
        "cd_cargo": ["CD_CARGO_PERGUNTA", "CD_CARGO"],
        "ds_cargo": ["DS_CARGO_PERGUNTA", "DS_CARGO"],
        "ds_tipo_votavel": ["DS_TIPO_VOTAVEL"],
        "nr_votavel": ["NR_VOTAVEL"],
        "nm_votavel": ["NM_VOTAVEL"],
        "sg_partido": ["SG_PARTIDO"],
        "qt_votos": ["QT_VOTOS"],
        "qt_aptos": ["QT_APTOS"],
        "qt_comparecimento": ["QT_COMPARECIMENTO"],
        "qt_abstencoes": ["QT_ABSTENCOES"],
        "ts_abertura": ["DT_ABERTURA"],
        "ts_encerramento": ["DT_ENCERRAMENTO"],
        "ts_emissao_bu": ["DT_EMISSAO_BU"],
        "ts_bu_recebido": ["DT_BU_RECEBIDO"],
    }


# Códigos de tipo de votável (iguais em 2016–2024).
TIPOS_VOTAVEL = {1: "Nominal", 2: "Branco", 3: "Nulo", 4: "Legenda"}


@registrar(2016)
class BwebTXT2016(BwebCSV):
    """2016: TXT sem cabeçalho; colunas pela especificação "BU na Web" de 2015."""

    extensao_dados = ".txt"
    # Em 2016 o recebimento do BU também vem em horário local (o 1º BU do AC chega
    # às 17:05, minutos após o encerramento das 17:00 locais).
    horario_local = Formato.horario_local + ("ts_bu_recebido",)
    nomes = [
        "DT_GERACAO", "HH_GERACAO", "CD_PLEITO", "CD_ELEICAO", "SG_UF",
        "CD_CARGO_PERGUNTA", "DS_CARGO_PERGUNTA", "NR_ZONA", "NR_SECAO",
        "NR_LOCAL_VOTACAO", "NR_PARTIDO", "SG_PARTIDO", "CD_MUNICIPIO",
        "NM_MUNICIPIO", "DT_BU_RECEBIDO", "QT_APTOS", "QT_ABSTENCOES",
        "QT_COMPARECIMENTO", "CD_TIPO_ELEICAO", "CD_TIPO_URNA", "DS_TIPO_URNA",
        "NR_VOTAVEL", "NM_VOTAVEL", "QT_VOTOS", "CD_TIPO_VOTAVEL",
        "NR_URNA_EFETIVADA", "CD_CARGA_1_URNA_EFETIVADA", "CD_CARGA_2_URNA_EFETIVADA",
        "DT_CARGA_URNA_EFETIVADA", "CD_FLASHCARD_URNA_EFETIVADA", "DS_CARGO_PERGUNTA_SECAO",
    ]
    mapa = {**BwebCSV.mapa, "ds_tipo_votavel": ["CD_TIPO_VOTAVEL"]}

    def expressao(self, canonica, bruta, constante):
        if canonica == "ds_tipo_votavel" and bruta:
            casos = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in TIPOS_VOTAVEL.items())
            return f"CASE trim({self._q(bruta)}) {casos} END"
        return super().expressao(canonica, bruta, constante)
