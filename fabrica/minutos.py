"""Contador del total de minutos de Actions por día y por workflow (tarea #24).

Una vez al día (el primer ciclo con trabajo del día GT) se lee con `gh api` lo que gastó el día anterior,
ya completo: cada job se redondea hacia arriba al minuto, como lo cobra GitHub, y se suma por workflow.
Va a `estado/actions-minutos.json` bajo `total_por_dia` (las claves de fecha de siempre no se tocan).
Si el total del mes pasa de TOPE_MES, la fábrica se pausa con alerta roja. Sin red en las pruebas: `gh` se inyecta.
"""
from __future__ import annotations

import json
import math
import subprocess
from datetime import date, datetime, timedelta, timezone

from .config import raiz_repo
from .registro import ZONA

CLAVE = "total_por_dia"
TOPE_MES = 2400
CONSERVAR_DIAS = 40


def gh_api(ruta: str):
    """Transporte real: `gh api <ruta>` (usa GH_TOKEN) y devuelve el JSON."""
    r = subprocess.run(["gh", "api", ruta], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(r.stdout)


def _t(texto) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return None


def minutos_de_job(job: dict) -> int:
    """Segundos de reloj redondeados hacia arriba a minuto; 0 si el job no empezó o no terminó."""
    ini, fin = _t(job.get("started_at")), _t(job.get("completed_at"))
    if ini is None or fin is None or fin < ini:
        return 0
    return max(1, math.ceil((fin - ini).total_seconds() / 60))


def _paginas(gh, ruta: str, lista: str) -> list[dict]:
    out, pagina = [], 1
    while pagina <= 20:
        datos = gh(f"{ruta}{'&' if '?' in ruta else '?'}per_page=100&page={pagina}") or {}
        trozo = datos.get(lista) or []
        out += trozo
        if len(trozo) < 100:
            break
        pagina += 1
    return out


def contar_dia(gh, repo: str, dia: date) -> dict:
    """{'total': n, 'por_workflow': {nombre: n}} de las corridas creadas ese día GT."""
    ini = datetime(dia.year, dia.month, dia.day, tzinfo=ZONA).astimezone(timezone.utc)
    fin = ini + timedelta(days=1) - timedelta(seconds=1)
    rango = f"{ini:%Y-%m-%dT%H:%M:%SZ}..{fin:%Y-%m-%dT%H:%M:%SZ}"
    por = {}
    for corrida in _paginas(gh, f"repos/{repo}/actions/runs?created={rango}", "workflow_runs"):
        trabajos = _paginas(gh, f"repos/{repo}/actions/runs/{corrida['id']}/jobs?filter=all", "jobs")
        m = sum(minutos_de_job(j) for j in trabajos)
        nombre = str(corrida.get("name") or corrida.get("path") or "?")
        por[nombre] = por.get(nombre, 0) + m
    return {"total": sum(por.values()), "por_workflow": dict(sorted(por.items()))}


def total_del_mes(datos: dict, dia: date) -> int:
    prefijo = f"{dia.year:04d}-{dia.month:02d}-"
    return sum(int(v.get("total", 0)) for k, v in (datos.get(CLAVE) or {}).items() if k.startswith(prefijo))


def contar_ayer(raiz, ahora: datetime, repo: str, gh=gh_api) -> dict | None:
    """Cuenta el día GT anterior si aún no está en el archivo. Devuelve {'dia','total','mes'} o None si ya estaba."""
    ruta = raiz_repo(raiz) / "estado" / "actions-minutos.json"
    datos = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    if not isinstance(datos, dict):
        datos = {}
    ayer = ahora.astimezone(ZONA).date() - timedelta(days=1)
    tabla = datos.get(CLAVE) if isinstance(datos.get(CLAVE), dict) else {}
    if ayer.isoformat() in tabla:
        return None
    tabla[ayer.isoformat()] = contar_dia(gh, repo, ayer)
    corte = (ayer - timedelta(days=CONSERVAR_DIAS)).isoformat()
    datos[CLAVE] = {k: v for k, v in sorted(tabla.items()) if k >= corte}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"dia": ayer.isoformat(), "total": tabla[ayer.isoformat()]["total"], "mes": total_del_mes(datos, ayer)}
