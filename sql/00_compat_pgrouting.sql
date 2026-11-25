-- 00_compat_pgrouting.sql
-- ---------------------------------------------------------------------------
-- pgr_createTopology fue DEPRECADA en pgRouting 3.8 y ELIMINADA en 4.0
-- (la versión que trae Ubuntu 26.04: postgresql-18-pgrouting 4.0.1).
-- En pgRouting 3.x la función estaba escrita en PL/pgSQL, por lo que aquí se
-- reimplementa con la MISMA firma y semántica:
--
--   pgr_createTopology(edge_table, tolerance, the_geom, id,
--                      source, target, rows_where, clean) -> 'OK' | 'FAIL'
--
--   * Crea <edge_table>_vertices_pgr(id, cnt, chk, ein, eout, the_geom).
--   * Recorre las aristas (filtradas por rows_where) en orden de id; para cada
--     extremo busca un vértice existente a distancia <= tolerance (unidades de
--     la geometría; aquí metros, EPSG:32719). Si existe lo reutiliza, si no lo
--     crea. Escribe source / target en la tabla de aristas.
--   * clean = true borra la tabla de vértices y source/target previos.
--
-- Si el servidor tiene pgRouting 3.x (función nativa), NO se sobrescribe.
-- ---------------------------------------------------------------------------
DO $outer$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE p.proname = 'pgr_createtopology' AND n.nspname <> 'public') THEN
    RAISE NOTICE 'pgr_createTopology nativa disponible: no se crea la versión compatible';
    RETURN;
  END IF;

  EXECUTE $fn$
  CREATE OR REPLACE FUNCTION public.pgr_createTopology(
      edge_table text,
      tolerance  double precision,
      the_geom   text    DEFAULT 'the_geom',
      id         text    DEFAULT 'id',
      source     text    DEFAULT 'source',
      target     text    DEFAULT 'target',
      rows_where text    DEFAULT 'true',
      clean      boolean DEFAULT false)
  RETURNS varchar
  LANGUAGE plpgsql VOLATILE STRICT AS $body$
  DECLARE
    vtab    text := edge_table || '_vertices_pgr';
    srid    integer;
    r       record;
    p_ini   geometry;
    p_fin   geometry;
    v_src   bigint;
    v_tgt   bigint;
    n_proc  bigint := 0;
  BEGIN
    RAISE NOTICE 'pgr_createTopology(''%'', %, ''%'', ''%'', ''%'', ''%'', rows_where := ''%'', clean := %)',
                 edge_table, tolerance, the_geom, id, source, target, rows_where, clean;

    EXECUTE format('SELECT ST_SRID(%I) FROM %I WHERE %I IS NOT NULL LIMIT 1',
                   the_geom, edge_table, the_geom) INTO srid;
    IF srid IS NULL THEN
      RAISE NOTICE 'La tabla % no tiene geometrías', edge_table;
      RETURN 'FAIL';
    END IF;

    IF clean THEN
      EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', vtab);
      EXECUTE format('UPDATE %I SET %I = NULL, %I = NULL', edge_table, source, target);
    END IF;

    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS %I (
          id bigserial PRIMARY KEY,
          cnt integer, chk integer, ein integer, eout integer,
          the_geom geometry(Point, %s))', vtab, srid);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I USING gist (the_geom)',
                   vtab || '_the_geom_idx', vtab);

    FOR r IN EXECUTE format(
        'SELECT %I AS eid, ST_StartPoint(%I) AS a, ST_EndPoint(%I) AS b
           FROM %I WHERE %I IS NOT NULL AND (%s) ORDER BY %I',
        id, the_geom, the_geom, edge_table, the_geom, rows_where, id)
    LOOP
      p_ini := r.a;  p_fin := r.b;

      -- extremo inicial
      EXECUTE format('SELECT id FROM %I WHERE ST_DWithin(the_geom, $1, $2)
                      ORDER BY the_geom <-> $1 LIMIT 1', vtab)
        INTO v_src USING p_ini, tolerance;
      IF v_src IS NULL THEN
        EXECUTE format('INSERT INTO %I (the_geom) VALUES ($1) RETURNING id', vtab)
          INTO v_src USING p_ini;
      END IF;

      -- extremo final
      EXECUTE format('SELECT id FROM %I WHERE ST_DWithin(the_geom, $1, $2)
                      ORDER BY the_geom <-> $1 LIMIT 1', vtab)
        INTO v_tgt USING p_fin, tolerance;
      IF v_tgt IS NULL THEN
        EXECUTE format('INSERT INTO %I (the_geom) VALUES ($1) RETURNING id', vtab)
          INTO v_tgt USING p_fin;
      END IF;

      EXECUTE format('UPDATE %I SET %I = $1, %I = $2 WHERE %I = $3',
                     edge_table, source, target, id)
        USING v_src, v_tgt, r.eid;
      n_proc := n_proc + 1;
    END LOOP;

    -- cnt = número de aristas que inciden en el vértice (igual que pgRouting 3.x)
    EXECUTE format(
      'UPDATE %1$I v SET cnt = s.c FROM (
          SELECT vid, count(*) c FROM (
             SELECT %2$I AS vid FROM %3$I WHERE %2$I IS NOT NULL
             UNION ALL
             SELECT %4$I FROM %3$I WHERE %4$I IS NOT NULL) x
          GROUP BY vid) s
       WHERE v.id = s.vid', vtab, source, edge_table, target);

    RAISE NOTICE 'Aristas procesadas: %', n_proc;
    EXECUTE format('SELECT count(*) FROM %I', vtab) INTO n_proc;
    RAISE NOTICE 'Vértices en %: %', vtab, n_proc;
    RETURN 'OK';
  END;
  $body$;
  $fn$;
END
$outer$;
