"""Línea de comandos del RAG. Desde bot/, con el entorno activado:

    python -m src.rag ingestar                         # el comando único de la ingesta (config estructural)
    python -m src.rag ingestar --config fijo --carpeta corpus/provisorio
    python -m src.rag buscar "¿cuál es el stop máximo?" --rol lector [-k 5] [--modo vectorial]
    python -m src.rag responder "¿cuál es el stop máximo?" --rol operador [--simulado]
    python -m src.rag medir [-k 3]                     # 10 preguntas x 2 fragmentados x 2 modos
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .busqueda import buscar, coleccion_por_defecto
from .embeddings import EmbedderLocal
from .fragmentado import CONFIG_POR_DEFECTO, CONFIGS
from .ingesta import CARPETA_CORPUS, RAIZ, cliente_chroma, ingestar
from .medicion import (COLECCIONES, comparar_hibrida, ganadora, medir, medir_con_llm, registro_por_consulta,
                       tabla_markdown, PREGUNTAS)
from .permisos import Rol, Usuario
from .respuesta import responder


def _entrada_de_consulta(consulta: str) -> dict:
    """Arma una Entrada de la PE2 con la foto de mercado de la primera consulta demo y la pregunta."""
    demo = json.loads((RAIZ / "datos" / "consultas_demo.json").read_text(encoding="utf-8"))
    base = (demo["consultas"] if isinstance(demo, dict) else demo)[0]
    return {**base, "id_propuesta": "PRP-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-9001",
            "consulta": consulta}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.rag", description="RAG con permisos (PE3)")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("ingestar", help="lee, fragmenta y guarda el corpus en ChromaDB")
    p.add_argument("--carpeta", default=str(CARPETA_CORPUS))
    p.add_argument("--config", choices=list(CONFIGS), default=CONFIG_POR_DEFECTO)

    for nombre in ("buscar", "responder"):
        p = sub.add_parser(nombre)
        p.add_argument("consulta")
        p.add_argument("--rol", choices=[r.value for r in Rol], required=True)
        p.add_argument("-k", type=int, default=5)
        p.add_argument("--config", choices=list(CONFIGS), default=CONFIG_POR_DEFECTO)
        if nombre == "buscar":
            p.add_argument("--modo", choices=["hibrida", "vectorial", "bm25"], default="hibrida")
        else:
            p.add_argument("--simulado", action="store_true", help="LLM falso que cita el primer fragmento")

    p = sub.add_parser("medir", help="top-k de las 10 preguntas con dos fragmentados")
    p.add_argument("-k", type=int, default=3)
    p.add_argument("--con-llm", action="store_true",
                   help="corre las 10 preguntas con el LLM real (necesita clave)")
    p.add_argument("--salida", default=str(RAIZ / "datos" / "rag" / "medicion.json"),
                   help="dónde guardar el registro por consulta (esperado y observado)")
    p.add_argument("--carpeta", default=str(CARPETA_CORPUS))

    args = parser.parse_args(argv)
    load_dotenv(RAIZ / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.comando == "ingestar":
        coleccion = ingestar(Path(args.carpeta), args.config)
        print(f"Colección {coleccion.name}: {coleccion.count()} fragmentos en {RAIZ / 'indice'}")
        return 0

    if args.comando == "buscar":
        usuario = Usuario(id=f"cli-{args.rol}", rol=Rol(args.rol))
        for r in buscar(args.consulta, usuario, args.k, coleccion=coleccion_por_defecto(args.config), modo=args.modo):
            print(f"\n[{r.puntaje:.4f}] {r.fragmento_id}  ({r.seccion})  roles={r.roles}  "
                  f"sim={r.similitud}  bm25={r.puntaje_bm25}")
            print("   " + r.texto[:300].replace("\n", "\n   "))
        return 0

    if args.comando == "responder":
        from ..motor import proveedores_desde_entorno
        from ..proveedores import ProveedorSimulado

        usuario = Usuario(id=f"cli-{args.rol}", rol=Rol(args.rol))
        coleccion = coleccion_por_defecto(args.config)
        if args.simulado:
            primero = buscar(args.consulta, usuario, 1, coleccion=coleccion)
            cita = [primero[0].fragmento_id] if primero else []
            proveedores = [ProveedorSimulado([json.dumps(dict(
                clasificacion="sin_senal", accion="no_operar", precio_entrada=None, stop_loss=None,
                take_profit=None, confianza=0.5, motivo="Simulado: cita el primer fragmento recuperado.",
                respuesta="(respuesta simulada)", fuentes=cita, requiere_humano=False))])]
        else:
            proveedores = proveedores_desde_entorno()
            if not proveedores:
                print("No hay claves de LLM en el .env. Usá --simulado.", file=sys.stderr)
                return 1
        salida = asyncio.run(responder(_entrada_de_consulta(args.consulta), usuario, proveedores=proveedores,
                                       coleccion=coleccion, k=args.k))
        print(salida.model_dump_json(indent=2))
        return 0

    # medir
    embedder = EmbedderLocal()
    preguntas = json.loads(PREGUNTAS.read_text(encoding="utf-8"))["preguntas"]
    resultados = medir(cliente_chroma(), embedder, Path(args.carpeta), k=args.k)
    print("\n" + tabla_markdown(resultados, args.k))
    g = ganadora(resultados)
    print(f"\nMejor en esta corrida: {g.config} ({g.modo}), Recall@{args.k} {g.aciertos('normal')[0]}/{g.aciertos('normal')[1]}, "
          f"MRR {g.mrr:.2f}")
    for r in resultados:
        print(f"\n## {r.config} / {r.modo}")
        for f in r.filas:
            print(f"- {f.id} {f.tipo:13} {'OK ' if f.acierto else 'MAL'} pos={f.posicion}  {f.detalle}")
    registro = {"k": args.k, "ganadora": f"{g.config}/{g.modo}",
                "por_consulta": registro_por_consulta(resultados, COLECCIONES, preguntas, embedder, args.k)}

    coleccion = ingestar(Path(args.carpeta), g.config, embedder=embedder)  # deja la ganadora como índice activo
    print(f"\n## Códigos exactos: posición del primer fragmento que contiene el código (top {args.k})")
    registro["codigos_exactos"] = comparar_hibrida(coleccion, embedder, k=args.k)
    for fila in registro["codigos_exactos"]:
        print(f"- {fila['id']} {fila['consulta']!r:45} vectorial={fila['vectorial']['posicion']} "
              f"bm25={fila['bm25']['posicion']} híbrida={fila['hibrida']['posicion']}")

    if args.con_llm:
        from ..motor import proveedores_desde_entorno

        proveedores = proveedores_desde_entorno()
        if not proveedores:
            print("--con-llm necesita BOT_ANTHROPIC_API_KEY u OPENAI_API_KEY en el .env", file=sys.stderr)
            return 1
        print(f"\n## Con el LLM ({proveedores[0].nombre}/{proveedores[0].modelo}): las 10 preguntas")
        registro["con_llm"] = asyncio.run(medir_con_llm(coleccion, preguntas, proveedores, embedder))
        for f in registro["con_llm"]:
            extra = (f"en prompt={f['restringido_en_prompt']} en fuentes={f['restringido_en_fuentes']}"
                     if f["tipo"] == "restringida" else f"fuentes={','.join(f['fuentes']) or '—'}")
            print(f"- {f['id']} {f['tipo']:13} {'OK ' if f['acierto'] else 'MAL'} requiere_humano={f['requiere_humano']} "
                  f"{extra} | {(f['respuesta'] or '—')[:140]}")

    Path(args.salida).write_text(json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nRegistro por consulta: {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
