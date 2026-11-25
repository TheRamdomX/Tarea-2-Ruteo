"""ATTR (Average Two-Terminal Reliability) para la red de fibra chilena.

ATTR = (2 / (K(K-1))) * sum_{i<j} P[i y j conectados], con K = nodos clave
(Neumayer & Modiano 2010; Neumayer et al. 2011).

Uso CLI:  python scripts/attr.py [-n N]
"""
import argparse
import os
import sys
import time

import numpy as np
import psycopg2
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components


def conectar(dbname=None):
    """Conexion a PostgreSQL usando variables de entorno (con valores por defecto)."""
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", "postgres"),
        dbname=dbname or os.environ.get("T2_DB", "resiliencia"),
    )


def cargar_grafo(conn, escenario="operacion"):
    """Devuelve (src, tgt, p, claves) para un escenario.

    - escenario 'operacion': p = 0 en todos los enlaces.
    - escenario 'sismo': p = p_falla de la tabla edges.
    claves: lista [(cod, vertex_id)] en orden alfabetico de cod (vertex_id puede ser None).
    """
    cur = conn.cursor()
    if escenario == "operacion":
        cur.execute("SELECT source, target, 0.0 FROM edges "
                    "WHERE operativo AND source IS NOT NULL AND target IS NOT NULL")
    elif escenario == "sismo":
        cur.execute("SELECT source, target, p_falla FROM edges "
                    "WHERE operativo AND source IS NOT NULL AND target IS NOT NULL")
    else:
        raise ValueError("escenario desconocido: %s" % escenario)
    filas = cur.fetchall()
    src = np.array([f[0] for f in filas], dtype=np.int64)
    tgt = np.array([f[1] for f in filas], dtype=np.int64)
    p = np.array([float(f[2]) for f in filas], dtype=np.float64)
    cur.execute("SELECT cod, vertex_id FROM nodos_clave ORDER BY cod")
    claves = [(c, v) for c, v in cur.fetchall()]
    cur.close()
    return src, tgt, p, claves


def _pares_y_attr(labels, claves_idx_valid, K):
    """labels: etiqueta de componente por nodo clave (K,), -1 = desconectado.
    Devuelve matriz booleana KxK de conexion."""
    L = labels
    E = (L[:, None] == L[None, :]) & (L[:, None] >= 0)
    return E


def attr(src, tgt, p, claves, n_iter=10000, seed=2010):
    """Calcula ATTR. Devuelve dict con attr, ic95, metodo, n_iter y pares (KxK)."""
    K = len(claves)
    if K < 2:
        raise ValueError("Se requieren al menos 2 nodos clave")
    vids = [v for _, v in claves]
    valid = np.array([v is not None for v in vids])
    vid_arr = np.array([v if v is not None else -1 for v in vids], dtype=np.int64)
    iu = np.triu_indices(K, 1)
    factor = 2.0 / (K * (K - 1))
    # Nodos clave sin vertice: siempre desconectados.
    sin_vertice = np.where(~valid)[0]

    n = 1
    if len(src):
        n = max(n, int(src.max()) + 1, int(tgt.max()) + 1)
    if valid.any():
        n = max(n, int(vid_arr[valid].max()) + 1)

    if np.all(p == 0):
        m = (src != tgt)
        g = csr_matrix((np.ones(int(m.sum())), (src[m], tgt[m])), shape=(n, n))
        _, lab = connected_components(g, directed=False)
        L = np.where(valid, lab[np.clip(vid_arr, 0, n - 1)], -1 - np.arange(K))
        L = L.astype(np.int64)
        E = _pares_y_attr(L, valid, K)
        val = factor * E[iu].sum()
        return {
            "attr": float(val),
            "ic95": (float(val), float(val)),
            "metodo": "exacto (p=0)",
            "n_iter": 1,
            "pares": E.astype(float),
        }

    # Contraccion de enlaces con p == 0 en supernodos.
    cero = p == 0
    g0 = csr_matrix((np.ones(int(cero.sum())), (src[cero], tgt[cero])), shape=(n, n))
    _, sup = connected_components(g0, directed=False)
    n_sup = int(sup.max()) + 1

    vivos = ~cero
    s = sup[src[vivos]]
    t = sup[tgt[vivos]]
    pk = p[vivos]
    no_lazo = s != t
    s, t, pk = s[no_lazo], t[no_lazo], pk[no_lazo]
    m_k = len(pk)

    ks = sup[np.clip(vid_arr, 0, n - 1)]
    ks = np.where(valid, ks, -1)

    rng = np.random.default_rng(seed)
    pares_sum = np.zeros((K, K), dtype=np.float64)
    muestras = np.empty(n_iter, dtype=np.float64)
    inicio = time.time()
    for it in range(n_iter):
        vive = rng.random(m_k) >= pk
        g = csr_matrix((np.ones(int(vive.sum())), (s[vive], t[vive])), shape=(n_sup, n_sup))
        _, lab = connected_components(g, directed=False)
        L = np.where(valid, lab[np.clip(ks, 0, n_sup - 1)], -1 - np.arange(K))
        L = L.astype(np.int64)
        E = _pares_y_attr(L, valid, K)
        pares_sum += E
        muestras[it] = factor * E[iu].sum()
        if (it + 1) % 2000 == 0:
            print("  iteraciones %d/%d (%.1f s)" % (it + 1, n_iter, time.time() - inicio))

    media = float(muestras.mean())
    ic = 1.96 * float(muestras.std(ddof=1)) / np.sqrt(n_iter) if n_iter > 1 else 0.0
    return {
        "attr": media,
        "ic95": (float(media - ic), float(media + ic)),
        "metodo": "Monte Carlo (N=%d, seed=%d)" % (n_iter, seed),
        "n_iter": n_iter,
        "pares": pares_sum / n_iter,
    }


def main():
    ap = argparse.ArgumentParser(description="Calcula ATTR operacion y sismo.")
    ap.add_argument("-n", type=int, default=10000, help="iteraciones Monte Carlo")
    args = ap.parse_args()
    conn = conectar()
    resultados = {}
    for esc in ("operacion", "sismo"):
        src, tgt, p, claves = cargar_grafo(conn, esc)
        print("Escenario %s: %d enlaces, %d nodos clave" % (esc, len(src), len(claves)))
        resultados[esc] = attr(src, tgt, p, claves, n_iter=args.n)
        r = resultados[esc]
        print("  ATTR = %.6f  IC95 [%.6f, %.6f]  %s" % (r["attr"], r["ic95"][0], r["ic95"][1], r["metodo"]))
    conn.close()
    a, b = resultados["operacion"]["attr"], resultados["sismo"]["attr"]
    if a:
        print("Diferencia relativa (sismo vs operacion): %.2f %%" % (100.0 * (b - a) / a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
