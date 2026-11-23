"""Una sola regla de "ficha publicable" y de "ficha descargable" (lista blanca)."""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlparse

from .config import cargar_yaml, raiz_repo
from .pausa import pausa_t_activa
from .vetados import fuentes_retiradas, logins_vetados

RUTA_FICHAS_T = "estado/fichas-t.yml"
# Un login de Twitch ([a-z0-9_]{3,25}) pasa "_" a "-": los ids viejos no cambian, y los logins con "_" al final o
# doble ("stephaniemx_", "a__b") dan "t-stephaniemx-", "t-a--b", que también valen (el login no trae guiones: no choca).
RE_ID_T = re.compile(r"^t-[a-z0-9][a-z0-9-]*$")


def id_ficha_t(login: str) -> str:
    """Id estable de la ficha T de un login de Twitch."""
    return "t-" + str(login).lower().replace("_", "-")



def _fuentes_yml(raiz=None) -> list[dict]:
    datos = cargar_yaml("fuentes.yml", raiz)
    return [f for f in (datos or []) if isinstance(f, dict)]


def fichas_t_guardadas(raiz=None) -> list[dict]:
    """Fichas T de estado/fichas-t.yml (sólo tipo T con id t-...). Archivo ausente o ilegible: lista vacía."""
    ruta = raiz_repo(raiz) / RUTA_FICHAS_T
    try:
        import yaml
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if isinstance(datos, dict):
        datos = datos.get("fichas")
    return [f for f in (datos or []) if isinstance(f, dict) and f.get("tipo") == "T"
            and isinstance(f.get("id"), str) and RE_ID_T.match(f["id"])]


def cargar_fuentes(raiz=None) -> list[dict]:
    """fuentes.yml más las fichas T generadas por el buscador.

    Sale vetada y no publicable: una ficha cuyo id esté en la columna «Fuente» de retiros.md, y una ficha T cuyo
    login esté vetado (config/vetados.yml o retiros.md). Con estado/pausa-t.yml toda ficha T generada sale
    pausada y no publicable (las de fuentes.yml no cambian)."""
    fichas = _fuentes_yml(raiz)
    ids = {f.get("id") for f in fichas}
    t = [f for f in fichas_t_guardadas(raiz) if f["id"] not in ids]
    vetados = logins_vetados(raiz)
    pausada = pausa_t_activa(raiz)[0]
    for f in t:
        if str(f.get("twitch_login", "")).lower() in vetados:
            f["estado"] = "vetada"
            f["publicable"] = False
        elif pausada:
            f["estado"] = "pausada"
            f["publicable"] = False
    fichas = fichas + t
    retiradas = fuentes_retiradas(raiz)
    for f in fichas:
        if str(f.get("id", "")).lower() in retiradas:
            f["estado"] = "vetada"
            f["publicable"] = False
    return fichas


def id_clip_twitch(url, canal: str | None = None) -> str | None:
    """Id de un clip de Twitch (clips.twitch.tv/<id> o twitch.tv/<canal>/clip/<id>, sólo https);
    None si no lo es o si `canal` se pide y no coincide."""
    if not isinstance(url, str):
        return None
    u = urlparse(url)
    if u.scheme != "https":
        return None
    host = (u.hostname or "").lower()
    partes = [x for x in u.path.split("/") if x]
    if host == "clips.twitch.tv" and len(partes) == 1:
        return partes[0]
    if host in ("twitch.tv", "www.twitch.tv", "m.twitch.tv") and len(partes) == 3 and partes[1] == "clip":
        if canal is not None and partes[0].lower() != canal.lower():
            return None
        return partes[2]
    return None


def _a_fecha(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def es_publicable(ficha: dict, hoy: date, plataforma: str | None = None,
                  pilar: str | None = None) -> tuple[bool, str]:
    """Verdadero sólo si la ficha cumple todo. El motivo dice cuál condición falló."""
    if ficha.get("estado") != "vigente":
        return False, f"estado {ficha.get('estado')!r}: debe ser vigente"
    if ficha.get("uso") != "produccion":
        return False, f"uso {ficha.get('uso')!r}: debe ser produccion"
    if ficha.get("publicable") is not True:
        return False, "publicable no es true"
    vence = ficha.get("vence")
    if vence is not None:
        f = _a_fecha(vence)
        if f is None:
            return False, f"vence ilegible ({vence!r})"
        if f < _a_fecha(hoy):
            return False, f"vencida el {f}"
    pr = ficha.get("presupuesto_restante")
    if pr == 0 or (isinstance(pr, str) and pr.strip().lower() == "agotado"):
        return False, "presupuesto agotado"
    reglas = ficha.get("reglas") or {}
    if plataforma is not None and plataforma not in (reglas.get("plataformas") or []):
        return False, f"plataforma {plataforma} no está en reglas.plataformas"
    pilares = ficha.get("pilares")
    if ficha.get("tipo") == "T":
        if not (isinstance(ficha.get("creador"), str) and ficha["creador"].startswith("twitch.tv/")):
            return False, "ficha T: el creador debe empezar por twitch.tv/"
        if not ficha.get("twitch_login"):
            return False, "ficha T: falta twitch_login"
    if ficha.get("tipo") == "D" and pilar != "informativa":
        return False, f"tipo D exige pilar informativa (recibió {pilar!r})"
    if pilar is not None and pilares and pilar not in pilares:
        return False, f"pilar {pilar} no está en los pilares de la ficha"
    return True, "ok"


def _es_https(u) -> bool:
    return isinstance(u, str) and u.startswith("https://") and len(u) > 8


def urls_material(ficha: dict) -> list[str]:
    """URL del material de la ficha. Cada entrada es el texto de la URL o {url, creador, etiquetas}."""
    material = ficha.get("material")
    if not isinstance(material, list):
        return []
    return [m.get("url") if isinstance(m, dict) else m for m in material]


def entrada_material(ficha: dict, url: str | None = None, creador: str | None = None) -> dict | None:
    """La entrada de `material` con esa URL (o con ese creador), siempre como diccionario; None si no hay."""
    for m in ficha.get("material") or []:
        if not isinstance(m, dict):
            continue
        if url is not None and m.get("url") == url:
            return m
        if url is None and creador is not None and str(m.get("creador", "")).strip().lower() == creador.strip().lower():
            return m
    return None


def etiqueta_creador(ficha: dict, creador: str | None, plataforma: str) -> str | None:
    """Cómo se etiqueta al creador recortado en esa red (`etiquetas` de su entrada de material), o None."""
    if not creador:
        return None
    m = entrada_material(ficha, None, creador)
    if m is None:
        return None
    e = (m.get("etiquetas") or {}).get(plataforma)
    return str(e).strip() if isinstance(e, str) and e.strip() else None


def puede_descargar(ficha: dict) -> tuple[bool, str]:
    tipo = ficha.get("tipo")
    if tipo == "C":
        return True, "material propio"
    if tipo == "D":
        return False, "tipo D no descarga video"
    if tipo == "T":
        return True, "ficha T: sólo el clip de Twitch"
    if tipo in ("A", "B"):
        material = urls_material(ficha)
        if material and all(_es_https(u) for u in material):
            return True, "la ficha trae material con URL https directas"
        if (ficha.get("reglas") or {}).get("permite_descarga") is True:
            return True, "reglas.permite_descarga es true"
        return False, "sin permite_descarga ni material: sólo enlace, no se descarga"
    return False, f"tipo {tipo!r} desconocido"


def autoriza_descarga(ficha: dict | None, hoy: date, uso: str, url: str | None = None,
                      pilar: str | None = None) -> tuple[bool, str]:
    """Puerta única ANTES de abrir una conexión. El motivo nombra la condición que falló."""
    if not isinstance(ficha, dict):
        return False, "ficha ausente: la fuente no está registrada en fuentes.yml"
    if ficha.get("estado") != "vigente":
        return False, f"estado {ficha.get('estado')!r}: debe ser vigente"
    vence = ficha.get("vence")
    if vence is not None:
        f = _a_fecha(vence)
        if f is None:
            return False, f"vence ilegible ({vence!r})"
        if f < _a_fecha(hoy):
            return False, f"vencida el {f}"
    ok, motivo = puede_descargar(ficha)
    if not ok:
        return False, f"no descargable: {motivo}"
    if uso == "produccion":
        ok, motivo = es_publicable(ficha, hoy, None, pilar)
        if not ok:
            return False, f"uso produccion no autorizado: {motivo}"
    elif uso == "muestras":
        if ficha.get("uso") != "muestras":
            ok, motivo = es_publicable(ficha, hoy, None, pilar)
            if not ok:
                return False, f"uso muestras no autorizado: la ficha no es de muestras ni publicable ({motivo})"
    else:
        return False, f"uso {uso!r} desconocido (produccion o muestras)"
    if ficha.get("tipo") == "T":
        if id_clip_twitch(url, ficha.get("twitch_login") or "") is None:
            return False, "ficha T sólo autoriza el clip de Twitch"
    material = urls_material(ficha) if ficha.get("material") is not None else None
    if material is not None and (url is None or url not in material):
        return False, f"url fuera de material: {url!r}"
    return True, "ok"


def hay_campana_cr(raiz=None) -> bool:
    """¿Hay alguna ficha vigente, publicable y con `cr_campaign_id`? Si no, el robot de envío no tiene qué hacer."""
    return any(f.get("estado") == "vigente" and f.get("publicable") is True and f.get("cr_campaign_id")
               for f in _fuentes_yml(raiz))


if __name__ == "__main__":      # `python3 -m fabrica.fuentes`: imprime cr=si|no (lo usa el primer paso de fabrica.yml)
    print("cr=" + ("si" if hay_campana_cr() else "no"))
