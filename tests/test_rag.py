"""Tests del RAG (PE3). Sin red ni claves: embedder falso y ChromaDB en memoria.

Lo que más pesa en la rúbrica (25 %): el filtro por rol va en la consulta a la
base, y un usuario sin permiso no recibe nada de un documento restringido, ni en
los resultados ni dentro del prompt que se le manda al modelo.
"""

from __future__ import annotations

import json
import pytest

from src.contratos import Salida
from src.proveedores import ProveedorSimulado, esquema_para_llm
from src.rag.busqueda import ResultadoBusqueda, buscar, fusionar_rrf, tokenizar
from src.rag.documentos import cargar_documentos
from src.rag.embeddings import EmbedderFalso
from src.rag.fragmentado import CONFIGS, fragmentar
from src.rag.ingesta import cliente_chroma, ingestar
from src.rag.permisos import Rol, Usuario, filtro_chroma
from src.rag.respuesta import ESQUEMA_SALIDA_RAG, SalidaConFuentes, responder

# Marca que solo existe en el documento restringido: si aparece donde no debe, el test falla.
SECRETO = "ZETA-7781"

OPERADOR = Usuario(id="op-1", rol=Rol.OPERADOR)
LECTOR = Usuario(id="lec-1", rol=Rol.LECTOR)


@pytest.fixture
def corpus(tmp_path):
    carpeta = tmp_path / "corpus"
    carpeta.mkdir()
    (carpeta / "reporte.md").write_text(
        "# Reporte diario\n\n## Rendimiento\n\nEl reporte diario compara la cartera contra el S&P 500 y las "
        "T-bills. Se envía antes de las 19:00 ART.\n\n## Stop\n\nEl reporte muestra cuántas órdenes "
        "salieron por stop en el día.\n", encoding="utf-8")
    (carpeta / "mandato.md").write_text(
        f"# Mandato de riesgo\n\n## Límites\n\nEl stop máximo por orden es {SECRETO}: más lejos, aprueba "
        f"el Operador. Código interno del límite: {SECRETO}.\n", encoding="utf-8")
    (carpeta / "sin_manifiesto.md").write_text(
        "# Nota suelta\n\nUn documento que nadie cargó en el manifiesto de permisos.\n", encoding="utf-8")
    (carpeta / "permisos.json").write_text(json.dumps({"documentos": {
        "reporte.md": {"titulo": "Reporte diario", "roles": ["operador", "lector"]},
        "mandato.md": {"titulo": "Mandato de riesgo", "roles": ["operador"]},
    }}), encoding="utf-8")
    return carpeta


@pytest.fixture
def coleccion(corpus):
    # Cliente en memoria, vaciado en cada test para que no compartan estado.
    cliente = cliente_chroma(None)
    cliente.reset()
    return ingestar(corpus, CONFIGS["estructural"], cliente=cliente, embedder=EmbedderFalso())


# ---------------------------------------------------------------- ingesta y metadatos

def test_cada_fragmento_lleva_documento_seccion_y_roles(coleccion):
    datos = coleccion.get(include=["metadatas"])
    assert datos["ids"]
    for meta in datos["metadatas"]:
        assert meta["documento"] and meta["seccion"]
        assert {"rol_operador", "rol_lector", "roles"} <= meta.keys()
    mandato = [m for m in datos["metadatas"] if m["documento"] == "mandato.md"]
    assert mandato and all(m["rol_operador"] and not m["rol_lector"] for m in mandato)
    assert any(m["seccion"] == "Mandato de riesgo > Límites" for m in mandato)


def test_documento_sin_manifiesto_queda_solo_para_el_operador(corpus):
    doc = next(d for d in cargar_documentos(corpus) if d.id == "sin_manifiesto.md")
    assert doc.roles == [Rol.OPERADOR]


def test_la_ingesta_es_idempotente(corpus, coleccion):
    antes = coleccion.count()
    otra = ingestar(corpus, CONFIGS["estructural"], cliente=cliente_chroma(None), embedder=EmbedderFalso())
    assert otra.count() == antes  # borra y vuelve a crear: no duplica fragmentos


# ---------------------------------------------------------------- permisos (un test por rol y más)

@pytest.mark.parametrize("modo", ["hibrida", "vectorial", "bm25"])
def test_lector_no_recibe_nada_del_restringido_ni_pidiendo_todo(coleccion, modo):
    # La consulta ES el texto secreto y se piden 1000 resultados: si hubiera post-filtrado, aparecería.
    resultados = buscar(f"stop máximo {SECRETO}", LECTOR, 1000, coleccion=coleccion,
                        embedder=EmbedderFalso(), modo=modo, candidatos=1000)
    assert all(r.documento != "mandato.md" for r in resultados)
    assert all(SECRETO not in r.texto for r in resultados)
    assert all(r.documento != "sin_manifiesto.md" for r in resultados)


@pytest.mark.parametrize("modo", ["hibrida", "vectorial", "bm25"])
def test_operador_si_recibe_el_restringido(coleccion, modo):
    resultados = buscar(f"stop máximo {SECRETO}", OPERADOR, 3, coleccion=coleccion,
                        embedder=EmbedderFalso(), modo=modo)
    assert resultados[0].documento == "mandato.md"


class ColeccionEspia:
    """Envuelve la colección y anota cada consulta a la base, para ver si lleva el filtro."""

    def __init__(self, coleccion):
        self._c, self.llamadas = coleccion, []
        self.metadata = coleccion.metadata

    def query(self, **kw):
        self.llamadas.append(("query", kw.get("where")))
        return self._c.query(**kw)

    def get(self, **kw):
        self.llamadas.append(("get", kw.get("where")))
        return self._c.get(**kw)


@pytest.mark.parametrize("usuario", [OPERADOR, LECTOR], ids=["operador", "lector"])
def test_el_filtro_por_rol_va_en_cada_consulta_a_la_base(coleccion, usuario):
    espia = ColeccionEspia(coleccion)
    buscar("stop", usuario, 5, coleccion=espia, embedder=EmbedderFalso())
    assert {tipo for tipo, _ in espia.llamadas} == {"query", "get"}  # las dos ramas consultan la base
    assert all(where == filtro_chroma(usuario) for _, where in espia.llamadas)


def _respuesta_modelo(fuentes: list[str], requiere_humano: bool = False, confianza: float = 0.8) -> str:
    return json.dumps(dict(respuesta="Se envía antes de las 19:00 ART.", motivo="Lo dice la sección de rendimiento.",
                           fuentes=fuentes, requiere_humano=requiere_humano, confianza=confianza))


async def test_lector_sin_permiso_no_recibe_nada_ni_en_el_prompt(coleccion, entrada_valida):
    """El test que pide la consigna: nada del restringido en los resultados NI en el prompt."""
    proveedor = ProveedorSimulado([_respuesta_modelo(["reporte.md#001"])])
    entrada = {**entrada_valida, "consulta": f"¿Cuál es el stop máximo {SECRETO} y cuándo sale el reporte?"}

    salida = await responder(entrada, LECTOR, proveedores=[proveedor], coleccion=coleccion,
                             embedder=EmbedderFalso(), umbral=0.0)

    assert len(proveedor.llamadas) == 1  # sí se llamó al modelo...
    prompt = json.dumps(proveedor.llamadas[0], ensure_ascii=False)
    assert "Reporte diario" in prompt  # ...con lo que el Lector puede leer
    assert "Mandato de riesgo" not in prompt and "mandato.md" not in prompt
    # El secreto aparece una sola vez: en la consulta que escribió el usuario, nunca en un fragmento.
    assert prompt.count(SECRETO) == 1
    assert "<fragmento" in prompt and all(SECRETO not in b for b in prompt.split("<fragmento")[1:-1])
    assert all(f.documento != "mandato.md" for f in salida.fuentes)


async def test_operador_recibe_el_restringido_en_el_prompt(coleccion, entrada_valida):
    proveedor = ProveedorSimulado([_respuesta_modelo(["mandato.md#000"])])
    entrada = {**entrada_valida, "consulta": f"¿Cuál es el stop máximo {SECRETO}?"}
    salida = await responder(entrada, OPERADOR, proveedores=[proveedor], coleccion=coleccion,
                             embedder=EmbedderFalso(), umbral=0.0)
    assert "Mandato de riesgo" in json.dumps(proveedor.llamadas[0], ensure_ascii=False)
    assert [f.documento for f in salida.fuentes] == ["mandato.md"]
    assert salida.requiere_humano is False


# ---------------------------------------------------------------- respuesta con fuentes

async def test_sin_evidencia_requiere_humano_y_no_llama_al_modelo(coleccion, entrada_valida):
    proveedor = ProveedorSimulado([])
    salida = await responder({**entrada_valida, "consulta": "¿Qué comisión cobra el bróker?"}, LECTOR,
                             proveedores=[proveedor], coleccion=coleccion, embedder=EmbedderFalso(), umbral=0.99)
    assert salida.requiere_humano is True
    assert salida.fuentes == [] and salida.accion.value == "no_operar"
    assert proveedor.llamadas == []  # no se gastó un token


async def test_cita_inventada_no_cuenta_y_requiere_humano(coleccion, entrada_valida):
    proveedor = ProveedorSimulado([_respuesta_modelo(["mandato.md#000", "no-existe#9"])])
    salida = await responder({**entrada_valida, "consulta": "¿Cuándo sale el reporte diario?"}, LECTOR,
                             proveedores=[proveedor], coleccion=coleccion, embedder=EmbedderFalso(), umbral=0.0)
    assert salida.fuentes == []  # mandato.md#000 no se recuperó para el Lector: no vale como fuente
    assert salida.requiere_humano is True


async def test_el_modelo_puede_pedir_una_persona(coleccion, entrada_valida):
    proveedor = ProveedorSimulado([_respuesta_modelo([], requiere_humano=True)])
    salida = await responder({**entrada_valida, "consulta": "¿Cuándo sale el reporte diario?"}, LECTOR,
                             proveedores=[proveedor], coleccion=coleccion, embedder=EmbedderFalso(), umbral=0.0)
    assert salida.requiere_humano is True


async def test_respuesta_valida_cita_documento_y_seccion(coleccion, entrada_valida):
    proveedor = ProveedorSimulado([_respuesta_modelo(["reporte.md#000"])])
    salida = await responder({**entrada_valida, "consulta": "¿Cuándo sale el reporte diario?"}, LECTOR,
                             proveedores=[proveedor], coleccion=coleccion, embedder=EmbedderFalso(), umbral=0.0)
    assert salida.requiere_humano is False
    assert salida.fuentes[0].documento == "reporte.md"
    assert salida.fuentes[0].seccion.startswith("Reporte diario")


async def test_la_confianza_la_calcula_el_codigo_y_no_el_modelo(coleccion, entrada_valida):
    """Lección de la PE2: la confianza declarada por el modelo cambia entre corridas."""
    consulta = {**entrada_valida, "consulta": "¿Cuándo sale el reporte diario?"}
    salidas = []
    for declarada in (0.45, 0.95):  # el modelo dice cualquier cosa...
        proveedor = ProveedorSimulado([_respuesta_modelo(["reporte.md#000"], confianza=declarada)])
        salidas.append(await responder(consulta, LECTOR, proveedores=[proveedor], coleccion=coleccion,
                                       embedder=EmbedderFalso(), umbral=0.0))
    assert salidas[0].confianza == salidas[1].confianza > 0  # ...y la confianza que cuenta no cambia
    assert [s.confianza_modelo for s in salidas] == [0.45, 0.95]  # la del modelo queda registrada


async def test_el_rag_nunca_propone_operar(coleccion, entrada_valida):
    proveedor = ProveedorSimulado([_respuesta_modelo(["reporte.md#000"])])
    salida = await responder({**entrada_valida, "consulta": "¿Hay entrada? ¿Cuándo sale el reporte?"}, LECTOR,
                             proveedores=[proveedor], coleccion=coleccion, embedder=EmbedderFalso(), umbral=0.0)
    assert salida.accion.value == "no_operar" and salida.precio_entrada is None
    esquema = json.dumps(ESQUEMA_SALIDA_RAG)
    assert "precio_entrada" not in esquema and "accion" not in esquema  # ni siquiera se le pide


def test_el_contrato_de_la_pe2_no_cambia():
    assert issubclass(SalidaConFuentes, Salida)
    propiedades = esquema_para_llm(Salida)["properties"]
    assert "requiere_humano" not in propiedades and "fuentes" not in propiedades


# ---------------------------------------------------------------- piezas

def test_rrf_premia_lo_que_aparece_arriba_en_las_dos_listas():
    def r(fid):
        return ResultadoBusqueda(documento="d", fragmento_id=fid, texto="", puntaje=0, titulo="t",
                                 seccion="s", roles="lector")
    fusion = fusionar_rrf([r("a"), r("b"), r("c")], [r("b"), r("d")])
    assert [x.fragmento_id for x in fusion][:2] == ["b", "a"]
    assert fusion[0].puntaje == pytest.approx(1 / 62 + 1 / 61, abs=1e-6)


def test_tokenizar_conserva_codigos_enteros_y_en_partes():
    tokens = tokenizar("Las reglas-v0.2 y ENTRADA_LEJANA")
    assert {"reglas-v0.2", "reglas", "v0", "2", "entrada_lejana", "entrada", "lejana"} <= set(tokens)
    assert "las" not in tokens and "y" not in tokens


def test_fragmentado_estructural_no_mezcla_secciones_y_el_fijo_si_corta(corpus):
    doc = next(d for d in cargar_documentos(corpus) if d.id == "reporte.md")
    estructurales = fragmentar(doc, CONFIGS["estructural"])
    assert [f.seccion for f in estructurales] == ["Reporte diario > Rendimiento", "Reporte diario > Stop"]
    assert all(f.texto.startswith("[Reporte diario > ") for f in estructurales)
    fijos = fragmentar(doc, CONFIGS["fijo"])
    assert all(len(f.texto.split("\n", 1)[1]) <= CONFIGS["fijo"].tamano for f in fijos)


def test_buscar_con_otro_embedder_falla_en_vez_de_devolver_basura(coleccion):
    class OtroEmbedder(EmbedderFalso):
        nombre = "otro-modelo"

    with pytest.raises(ValueError, match="no son comparables"):
        buscar("stop", LECTOR, 3, coleccion=coleccion, embedder=OtroEmbedder())


# ---------------------------------------------------------------- contexto del veto (03/11)

def test_buscar_por_documento_suma_el_filtro_al_del_rol(coleccion):
    espia = ColeccionEspia(coleccion)
    resultados = buscar("reporte stop", OPERADOR, 5, coleccion=espia, embedder=EmbedderFalso(),
                        documentos=["mandato.md"])
    assert resultados and {r.documento for r in resultados} == {"mandato.md"}
    esperado = {"$and": [filtro_chroma(OPERADOR), {"documento": {"$in": ["mandato.md"]}}]}
    assert all(where == esperado for _, where in espia.llamadas)  # el rol sigue en cada consulta


def test_buscar_por_documento_no_saltea_el_permiso(coleccion):
    # El Lector no puede leer el mandato: pedirlo por nombre no lo habilita.
    assert buscar("stop", LECTOR, 5, coleccion=coleccion, embedder=EmbedderFalso(), documentos=["mandato.md"]) == []


def test_el_contexto_del_veto_pone_el_mandato_primero_y_sin_repetir(coleccion):
    from src.motor import combinar_contexto_veto

    del_mandato = buscar("stop", OPERADOR, 3, coleccion=coleccion, embedder=EmbedderFalso(), documentos=["mandato.md"])
    libres = buscar("stop", OPERADOR, 3, coleccion=coleccion, embedder=EmbedderFalso())
    ctx = combinar_contexto_veto(del_mandato, libres)
    assert ctx[0].documento == "mandato.md"
    ids = [r.fragmento_id for r in ctx]
    assert len(ids) == len(set(ids)) and set(ids) == {r.fragmento_id for r in del_mandato + libres}


@pytest.mark.solo_full
def test_el_veto_busca_en_el_mandato_vigente():
    from src.motor import DOCUMENTOS_MANDATO
    from src.rag.ingesta import CARPETA_CORPUS

    assert DOCUMENTOS_MANDATO, "config/veto_mandato.json tiene que nombrar el mandato"
    for doc in DOCUMENTOS_MANDATO:  # si el corpus cambia de lugar, el filtro no puede quedar vacío en silencio
        assert (CARPETA_CORPUS / doc).is_file(), doc


def test_el_veto_busca_como_lector(monkeypatch):
    """Reparto de permisos de la mesa: el veto del modelo es Lector; no necesita los exports del Operador (M7)."""
    import src.rag.busqueda as b
    from types import SimpleNamespace as NS
    from src.motor import contexto_desde_rag

    roles = []
    monkeypatch.setattr(b, "coleccion_por_defecto", lambda *a, **k: object())
    monkeypatch.setattr(b, "buscar", lambda consulta, usuario, k, **kw: roles.append(usuario.rol) or [])
    contexto_desde_rag()(NS(activo="SPY", consulta="x"), NS(accion=NS(value="comprar")))
    assert roles and set(roles) == {Rol.LECTOR}


def test_sin_config_del_mandato_el_veto_avisa(monkeypatch, caplog):
    import src.motor as m
    import src.rag.busqueda as b
    from types import SimpleNamespace as NS

    monkeypatch.setattr(m, "DOCUMENTOS_MANDATO", ())
    monkeypatch.setattr(b, "coleccion_por_defecto", lambda *a, **k: object())
    monkeypatch.setattr(b, "buscar", lambda *a, **k: [])
    with caplog.at_level("WARNING"):
        ctx = m.contexto_desde_rag()
    assert ctx(NS(activo="SPY", consulta="x"), NS(accion=NS(value="comprar"))) == []
    assert "veto_mandato.json" in caplog.text
