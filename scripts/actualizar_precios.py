"""Trae precios de IOL y los publica en la rama 'datos' del repositorio.
Solo LEE precios: no usa ninguna función de compra o venta.
La clave llega por variables secretas: nunca se escribe acá."""
import json, os, shutil, subprocess, sys, tempfile, time
import urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "https://api.iolinversiones.com"
TIPOS = ["acciones", "cedears", "etfs", "bonos", "letras",
         "obligaciones_negociables", "indices", "monedas"]
PLAZO = "T1"       # T1 = 24hs
INTERVALO = 300    # segundos entre actualizaciones (5 minutos)


def llamar(ruta, params=None, cuerpo=None, token=None):
    url = BASE + ruta + ("?" + urllib.parse.urlencode(params) if params else "")
    cab = {"Accept": "application/json", "User-Agent": "prueba-aula/1.0"}
    datos = None
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode()
        cab["Content-Type"] = "application/json"
    if token:
        cab["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=datos, headers=cab), timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detalle = json.load(e).get("error")
        except Exception:
            detalle = None
        raise RuntimeError(f"HTTP {e.code} en {ruta}: {detalle}")


def traer(usuario, clave, primera):
    try:
        r = llamar("/auth/token", cuerpo={"grant_type": "password",
                                          "username": usuario, "password": clave})
        token = r["data"]["access_token"]
    except Exception as e:
        raise RuntimeError(f"No se pudo iniciar sesión en IOL ({e})")
    disponibles = {t["id"] for t in llamar("/broker/assets/types", {"country": "AR"}, token=token)["data"]}
    precios, resumen = {}, {}
    for tipo in TIPOS:
        if tipo not in disponibles:
            print(f"Aviso: el tipo '{tipo}' no existe, se omite.")
            continue
        try:
            paneles = llamar("/broker/assets/panels", {"country": "AR", "asset_type": tipo}, token=token)["data"]
        except Exception as e:
            print(f"[{tipo}] no se pudieron leer los paneles: {e}")
            continue
        if primera:
            print(f"[{tipo}] paneles:", ", ".join(p["id"] for p in paneles))
        for p in paneles:
            time.sleep(0.4)
            try:
                filas = llamar("/broker/market-data/screener", {"country": "AR", "asset_type": tipo,
                               "panel": p["id"], "term": PLAZO}, token=token)["data"]
            except Exception as e:
                print(f"  [{tipo}/{p['id']}] falló: {e}")
                continue
            for f in filas:
                i, q, b = f.get("instrument") or {}, f.get("quotes") or {}, f.get("book") or {}
                if not i.get("symbol"):
                    continue
                precios[i["symbol"]] = {
                    "nombre": i.get("name"), "tipo": tipo, "moneda": i.get("currency"),
                    "ultimo": q.get("last"), "apertura": q.get("open"),
                    "cierre_anterior": q.get("close"), "maximo": q.get("high"),
                    "minimo": q.get("low"), "variacion_pct": q.get("change_percent"),
                    "volumen": q.get("volume"),
                    "compra": (b.get("bid") or {}).get("price"),
                    "venta": (b.get("ask") or {}).get("price"),
                }
                resumen[tipo] = resumen.get(tipo, 0) + 1
    if not precios:
        raise RuntimeError("No llegó ningún precio")
    print("Activos:", len(precios), resumen)
    return {"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fuente": "IOL", "plazo": PLAZO, "precios": precios}


def publicar(salida):
    """Sube precios.json a la rama 'datos' (un solo commit, sin historial)."""
    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, "precios.json"), "w", encoding="utf-8") as f:
            json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
        repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
        if not repo or not tok:
            shutil.copy(os.path.join(tmp, "precios.json"), "precios.json")
            print("Guardado en ./precios.json (modo local)")
            return
        def git(*a):
            subprocess.run(["git", *a], cwd=tmp, check=True, capture_output=True)
        git("init", "-q"); git("checkout", "-q", "-b", "datos")
        git("config", "user.name", "github-actions[bot]")
        git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        git("add", "precios.json"); git("commit", "-q", "-m", "Precios " + salida["actualizado"])
        git("push", "-q", "--force", f"https://x-access-token:{tok}@github.com/{repo}.git", "datos")
        print("Publicado en la rama 'datos'")
    except subprocess.CalledProcessError:
        raise RuntimeError("No se pudo publicar en GitHub")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    usuario, clave = os.environ.get("IOL_USUARIO"), os.environ.get("IOL_CLAVE")
    if not usuario or not clave:
        sys.exit("Faltan los secretos IOL_USUARIO e IOL_CLAVE en GitHub.")
    fin = time.time() + int(os.environ.get("MINUTOS") or 0) * 60
    ciclo = 0
    while True:
        ciclo += 1
        try:
            publicar(traer(usuario, clave, ciclo == 1))
            print(f"Ciclo {ciclo} OK")
        except Exception as e:
            print(f"Ciclo {ciclo} con error: {e}")
            if ciclo == 1:
                sys.exit("Falló el primer intento. Puede ser usuario/clave, la verificación en dos pasos "
                         "o un bloqueo de IOL a los servidores de GitHub.")
        if time.time() + INTERVALO > fin:
            break
        time.sleep(INTERVALO)


main()
