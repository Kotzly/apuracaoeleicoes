-- Exemplos de consulta (DuckDB / SQL Lab do Superset).
-- Exemplo: presidente, 1º turno de 2022 (cd_eleicao = 544, cd_cargo = 1).
-- Para trocar de nível, troque as colunas do PARTITION BY / GROUP BY:
--   Brasil:            (nada)
--   UF:                sg_uf
--   município:         sg_uf, cd_municipio, nm_municipio
--   zona:              sg_uf, nr_zona            -- zona pode abranger vários municípios
--   município × zona:  sg_uf, cd_municipio, nr_zona

-- 1) Evolução: votos acumulados por candidato ao longo do recebimento dos BUs, por zona
SELECT sg_uf, nr_zona, nr_votavel, nm_votavel, ts_bu_recebido,
       sum(sum(qt_votos)) OVER (
           PARTITION BY sg_uf, nr_zona, nr_votavel
           ORDER BY ts_bu_recebido) AS votos_acumulados
FROM votos
WHERE cd_eleicao = 544 AND cd_cargo = 1
GROUP BY sg_uf, nr_zona, nr_votavel, nm_votavel, ts_bu_recebido
ORDER BY sg_uf, nr_zona, nr_votavel, ts_bu_recebido;

-- 2) Evolução do % de seções recebidas, por município
SELECT sg_uf, cd_municipio, nm_municipio, ts_bu_recebido,
       sum(count(*)) OVER w_tempo                         AS secoes_recebidas,
       sum(count(*)) OVER w_tempo
         / sum(count(*)) OVER w_total * 100               AS pct_secoes,
       sum(sum(qt_comparecimento)) OVER w_tempo           AS comparecimento_acumulado
FROM secoes
WHERE cd_eleicao = 544 AND cd_cargo = 1
GROUP BY sg_uf, cd_municipio, nm_municipio, ts_bu_recebido
WINDOW w_tempo  AS (PARTITION BY sg_uf, cd_municipio ORDER BY ts_bu_recebido),
       w_total  AS (PARTITION BY sg_uf, cd_municipio)
ORDER BY sg_uf, cd_municipio, ts_bu_recebido;

-- 3) Consolidado (resultado final) por UF
SELECT sg_uf, nr_votavel, nm_votavel, sum(qt_votos) AS votos,
       sum(qt_votos) * 100.0 / sum(sum(qt_votos)) OVER (PARTITION BY sg_uf) AS pct_total
FROM votos
WHERE cd_eleicao = 544 AND cd_cargo = 1
GROUP BY sg_uf, nr_votavel, nm_votavel
ORDER BY sg_uf, votos DESC;

-- 4) Consolidado de comparecimento por zona
SELECT sg_uf, nr_zona, count(*) AS secoes, sum(qt_aptos) AS aptos,
       sum(qt_comparecimento) AS comparecimento,
       sum(qt_comparecimento) * 100.0 / sum(qt_aptos) AS pct_comparecimento
FROM secoes
WHERE cd_eleicao = 544 AND cd_cargo = 1
GROUP BY sg_uf, nr_zona
ORDER BY sg_uf, nr_zona;
