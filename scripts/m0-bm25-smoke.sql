\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS pg_search CASCADE;

DROP TABLE IF EXISTS m0_bm25_documents;
CREATE TABLE m0_bm25_documents (
    id integer PRIMARY KEY,
    content text NOT NULL,
    product_code text NOT NULL
);

INSERT INTO m0_bm25_documents (id, content, product_code) VALUES
    (1, '数据库默认端口为3306，配置文件位于config目录。', 'DB-3306'),
    (2, 'Web服务默认端口为8080，请检查防火墙策略。', 'WEB-8080'),
    (3, '安装完成后需要重启业务服务。', 'OPS-RESTART'),
    (4, '型号DB-3306的配置路径为/opt/ship/config/database.yml。', 'DB-3306-PATH');

CREATE INDEX m0_bm25_documents_search_idx
ON m0_bm25_documents
USING bm25 (id, (content::pdb.lindera(chinese)), product_code)
WITH (key_field='id');

DO $$
DECLARE
    top_id integer;
    model_top_id integer;
    path_top_id integer;
    token_count integer;
BEGIN
    SELECT cardinality('数据库默认端口'::pdb.lindera(chinese)::text[]) INTO token_count;
    IF token_count < 2 THEN
        RAISE EXCEPTION 'Chinese tokenizer returned too few terms: %', token_count;
    END IF;

    SELECT id INTO top_id
    FROM m0_bm25_documents
    WHERE content ||| '数据库 默认 端口 3306'
    ORDER BY pdb.score(id) DESC
    LIMIT 1;

    IF top_id IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'Unexpected BM25 top result: %', top_id;
    END IF;

    SELECT id INTO model_top_id
    FROM m0_bm25_documents
    WHERE content ||| 'DB-3306'
    ORDER BY pdb.score(id) DESC
    LIMIT 1;
    IF model_top_id IS DISTINCT FROM 4 THEN
        RAISE EXCEPTION 'Unexpected product-code top result: %', model_top_id;
    END IF;

    SELECT id INTO path_top_id
    FROM m0_bm25_documents
    WHERE content ||| '/opt/ship/config/database.yml'
    ORDER BY pdb.score(id) DESC
    LIMIT 1;
    IF path_top_id IS DISTINCT FROM 4 THEN
        RAISE EXCEPTION 'Unexpected path top result: %', path_top_id;
    END IF;
END
$$;

SELECT extname, extversion FROM pg_extension WHERE extname IN ('pg_search', 'vector') ORDER BY extname;
SELECT id, round(pdb.score(id)::numeric, 4) AS score
FROM m0_bm25_documents
WHERE content ||| '数据库 默认 端口 3306'
ORDER BY pdb.score(id) DESC;
