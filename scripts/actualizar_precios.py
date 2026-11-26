"""Trae precios de IOL y los publica en la rama 'datos' del repositorio.
Solo LEE precios: no usa ninguna función de compra o venta.
La clave llega por variables secretas: nunca se escribe acá."""
import html, json, os, re, shutil, subprocess, sys, tempfile, time
import urllib.parse, urllib.request, urllib.error
import email.utils
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

BASE = "https://api.iolinversiones.com"
TIPOS = ["acciones", "cedears", "etfs", "bonos", "obligaciones_negociables"]
PLAZO = "T1"       # T1 = 24hs
INTERVALO = 60     # segundos entre actualizaciones (1 minuto)
TOKEN = {}


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
        token = TOKEN["t"] = r["data"]["access_token"]
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


# ---------- Noticias (RSS) ----------
UA_NAV = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
FEEDS = [
    ("Investing.com", "https://es.investing.com/rss/news_25.rss", "es"),
    ("elEconomista", "https://www.eleconomista.es/rss/rss-mercados.php", "es"),
    ("The Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "en"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "en"),
]
NOTI = {"t": 0, "datos": None}
NOTI_CADA = 1800   # los feeds se vuelven a leer cada 30 minutos


def _limpio(s, tope=None):
    """Saca etiquetas HTML, decodifica entidades, compacta espacios y recorta en una palabra entera."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    if tope and len(s) > tope:
        s = s[:tope].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return s


def _fecha(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _hijo(nodo, *nombres):
    for c in nodo:
        if isinstance(c.tag, str) and c.tag.rsplit("}", 1)[-1] in nombres:
            return c
    return None


def _leer_feed(nombre, url, idioma):
    req = urllib.request.Request(url, headers={"User-Agent": UA_NAV,
                                               "Accept": "application/rss+xml, application/xml, text/xml, */*",
                                               "Accept-Language": "es,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        crudo = r.read(3000000)
    crudo = re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", crudo)   # '&' sueltos que rompen el XML
    raiz = ET.fromstring(crudo)
    salida = []
    for it in raiz.iter():
        if not isinstance(it.tag, str) or it.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        t, l = _hijo(it, "title"), _hijo(it, "link")
        d, f = _hijo(it, "description", "summary", "content"), _hijo(it, "pubDate", "published", "updated", "date")
        titulo = _limpio(t.text if t is not None else "", 220)
        enlace = ((l.get("href") or l.text or "") if l is not None else "").strip()
        if not titulo or not re.match(r"https?://", enlace, re.I):
            continue
        resumen = _limpio(d.text if d is not None else "", 240)
        if resumen == titulo:
            resumen = ""
        fe = _fecha(f.text if f is not None else "")
        salida.append({"titulo": titulo, "resumen": resumen, "url": enlace, "fuente": nombre, "idioma": idioma,
                       "fecha": fe.isoformat(timespec="seconds") if fe else ""})
        if len(salida) >= 20:
            break
    return salida


def noticias_traer():
    por = {"es": [], "en": []}
    vistos = set()
    for nombre, url, idioma in FEEDS:
        try:
            lista = _leer_feed(nombre, url, idioma)
            print(f"Noticias: {nombre} OK ({len(lista)})")
        except Exception as e:
            print(f"Noticias: {nombre} falló ({type(e).__name__}: {e})")
            continue
        for n in lista:
            if n["url"] in vistos:
                continue
            vistos.add(n["url"])
            por[idioma].append(n)
    for L in por.values():
        L.sort(key=lambda n: n["fecha"], reverse=True)
    es, en = por["es"], por["en"]
    items = es[:10] + en[:6]
    if len(items) < 16:   # si falta de un idioma, se completa con el otro
        usadas = {n["url"] for n in items}
        resto = sorted([n for n in es[10:] + en[6:] if n["url"] not in usadas], key=lambda n: n["fecha"], reverse=True)
        items += resto[:16 - len(items)]
    items.sort(key=lambda n: n["fecha"], reverse=True)
    return items


def noticias_previas():
    """Si no se pudo leer ningún feed, recupera el último noticias.json publicado."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    try:
        req = urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/datos/noticias.json?t={int(time.time())}",
                                     headers={"User-Agent": UA_NAV})
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.load(r)
        return j if isinstance(j.get("items"), list) and j["items"] else None
    except Exception as e:
        print("Sin noticias previas:", e)
        return None


def noticias():
    ahora = time.time()
    if NOTI["datos"] and ahora - NOTI["t"] < NOTI_CADA:
        return NOTI["datos"]
    try:
        items = noticias_traer()
    except Exception as e:
        print("Noticias con error:", e)
        items = None
    if items:
        NOTI["datos"] = {"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": items}
        NOTI["t"] = ahora
    else:
        NOTI["t"] = ahora - NOTI_CADA + 300   # se reintenta en 5 minutos
        if not NOTI["datos"]:
            NOTI["datos"] = noticias_previas()
    return NOTI["datos"]


def publicar(salida):
    """Sube precios.json a la rama 'datos' (un solo commit, sin historial)."""
    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, "precios.json"), "w", encoding="utf-8") as f:
            json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
        try:
            nt = noticias()
        except Exception as e:
            print("Noticias con error:", e)
            nt = None
        if nt:
            with open(os.path.join(tmp, "noticias.json"), "w", encoding="utf-8") as f:
                json.dump(nt, f, ensure_ascii=False, separators=(",", ":"))
        repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
        if not repo or not tok:
            shutil.copy(os.path.join(tmp, "precios.json"), "precios.json")
            if os.path.exists(os.path.join(tmp, "noticias.json")):
                shutil.copy(os.path.join(tmp, "noticias.json"), "noticias.json")
            print("Guardado en ./precios.json (modo local)")
            return
        def git(*a):
            subprocess.run(["git", *a], cwd=tmp, check=True, capture_output=True)
        git("init", "-q"); git("checkout", "-q", "-b", "datos")
        git("config", "user.name", "github-actions[bot]")
        git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        git("add", "-A"); git("commit", "-q", "-m", "Precios " + salida["actualizado"])
        git("push", "-q", "--force", f"https://x-access-token:{tok}@github.com/{repo}.git", "datos")
        print("Publicado en la rama 'datos'")
    except subprocess.CalledProcessError:
        raise RuntimeError("No se pudo publicar en GitHub")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


RE_USD = re.compile(r"usd|d[oó]lar|u\$s", re.I)
TASA = lambda d: 0.003 if d.get("tipo") == "letras" else 0.0071   # comisión + IVA (aprox.)


def operable(d):
    return (d.get("tipo") in ("acciones", "cedears", "etfs")
            and (d.get("ultimo") or 0) > 0)


PARES = [("AL30", "AL30D"), ("GD30", "GD30D"), ("AL35", "AL35D"), ("GD35", "GD35D")]


def mon_de(s, d, precios):
    """Cuenta en la que se opera: ARS (pesos), USD (dólar MEP, terminados en D) o CCL (dólar cable, terminados en C)."""
    if not RE_USD.search(d.get("moneda") or ""):
        return "ARS"
    return "CCL" if s.endswith("C") else "USD"


def tc(precios, tipo="MEP"):
    """Dólar implícito: se compra un bono en pesos y se vende su versión en dólares (D = MEP, C = cable/CCL)."""
    suf = "C" if tipo == "CCL" else "D"
    for a in ("AL30", "GD30", "AL35", "GD35"):
        pa, pb = precios.get(a), precios.get(a + suf)
        if pa and pb and pa.get("ultimo") and pb.get("ultimo"):
            f = lambda d, k: d.get(k) or d["ultimo"]
            return {"compra": f(pa, "venta") / f(pb, "compra"), "venta": f(pa, "compra") / f(pb, "venta"),
                    "mid": pa["ultimo"] / pb["ultimo"], "par": a}
    if tipo == "CCL":
        m = fxmed(precios)
        return {"compra": m, "venta": m, "mid": m, "par": "CEDEARs"}
    return None


def fxmed(precios):
    r = sorted(d["ultimo"] / precios[s + "C"]["ultimo"] for s, d in precios.items()
               if d.get("tipo") == "cedears" and d.get("ultimo") and (precios.get(s + "C") or {}).get("ultimo"))
    return r[len(r) // 2] if r else 1


def ejecutar(db, oref, o, px, tasa, mon="ARS", k=1):
    from firebase_admin import firestore
    uref = db.collection("users").document(o["uid"])
    campo = {"ARS": "cash", "USD": "usd", "CCL": "ccl"}[mon]

    @firestore.transactional
    def paso(tx):
        cur = oref.get(transaction=tx).to_dict()
        if not cur or cur.get("estado") != "pendiente":
            return False
        u = uref.get(transaction=tx).to_dict()
        pos, cash, n, s = dict(u.get("pos") or {}), u.get(campo, 0), o["cant"], o["s"]
        bruto = n * px * k
        com = round(bruto * tasa, 2)
        p = pos.get(s, {"c": 0, "costo": 0})
        if o["lado"] == "C":
            if bruto + com > cash:
                tx.update(oref, {"estado": "rechazada", "motivo": "Saldo insuficiente en " + {"ARS": "pesos.", "USD": "dólares MEP.", "CCL": "dólares CCL."}[mon]})
                return False
            cash -= bruto + com
            pos[s] = {"c": p["c"] + n, "costo": p["costo"] + bruto + com, "m": mon, "k": k}
        else:
            if n > p["c"]:
                tx.update(oref, {"estado": "rechazada", "motivo": "No había cantidad suficiente para vender."})
                return False
            cash += bruto - com
            resto = p["c"] - n
            if resto:
                pos[s] = {"c": resto, "costo": p["costo"] / p["c"] * resto, "m": mon, "k": k}
            else:
                pos.pop(s)
        ms = int(time.time() * 1000)
        ops = (u.get("ops") or []) + [{"t": ms, "s": s, "l": o["lado"], "c": n, "p": px, "com": com, "m": mon, "k": k}]
        tx.update(uref, {campo: round(cash, 2), "pos": pos, "ops": ops[-1000:]})
        tx.update(oref, {"estado": "ejecutada", "fill": {"px": px, "com": com, "t": ms}})
        return True
    return paso(db.transaction())


def cambio(db, oref, o, precios):
    """Compra o venta de dólares (MEP o CCL): dos operaciones, con comisión en cada una."""
    tipo = o.get("tc", "MEP")
    t = tc(precios, tipo)
    if not t:
        return False
    from firebase_admin import firestore
    uref = db.collection("users").document(o["uid"])
    campo = "ccl" if tipo == "CCL" else "usd"

    @firestore.transactional
    def paso(tx):
        cur = oref.get(transaction=tx).to_dict()
        if not cur or cur.get("estado") != "pendiente":
            return False
        u = uref.get(transaction=tx).to_dict()
        n, cash, usd = o["cant"], u["cash"], u.get(campo, 0)
        if o["lado"] == "CD":
            p = t["compra"]          # el alumno paga el precio de venta del mercado
            ars = n * p
            com = round(ars * 2 * 0.0071, 2)
            if ars + com > cash:
                tx.update(oref, {"estado": "rechazada", "motivo": "Pesos insuficientes."})
                return False
            cash -= ars + com
            usd += n
        else:
            p = t["venta"]           # el alumno recibe el precio de compra del mercado
            if n > usd:
                tx.update(oref, {"estado": "rechazada", "motivo": "Dólares insuficientes."})
                return False
            ars = n * p
            com = round(ars * 2 * 0.0071, 2)
            cash += ars - com
            usd -= n
        ms = int(time.time() * 1000)
        ops = (u.get("ops") or []) + [{"t": ms, "s": "USD", "l": o["lado"], "c": n, "p": round(p, 4), "com": com, "m": "ARS", "tc": tipo}]
        tx.update(uref, {"cash": round(cash, 2), campo: round(usd, 2), "ops": ops[-1000:]})
        tx.update(oref, {"estado": "ejecutada", "fill": {"px": round(p, 4), "com": com, "t": ms}})
        return True
    return paso(db.transaction())


def og(url):
    """Obtiene título e imagen de un enlace (vista previa)."""
    out = {"url": url, "titulo": "", "imagen": "", "sitio": ""}
    try:
        m = re.search(r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)([\w-]{11})", url)
        if m:
            out["imagen"] = f"https://img.youtube.com/vi/{m.group(1)}/hqdefault.jpg"
            out["sitio"] = "YouTube"
            req = urllib.request.Request("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe=""),
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                out["titulo"] = json.load(r).get("title", "")
            return out
        req = urllib.request.Request(url, headers={
            "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            "Accept-Language": "es"})
        with urllib.request.urlopen(req, timeout=10) as r:
            h = r.read(300000).decode("utf-8", "replace")

        def meta(k):
            for pat in (r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % k,
                        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % k):
                f = re.search(pat, h, re.I)
                if f:
                    return html.unescape(f.group(1)).strip()
            return ""
        t = re.search(r"<title[^>]*>([^<]*)", h, re.I)
        out["titulo"] = meta("og:title") or (html.unescape(t.group(1)).strip() if t else "")
        out["imagen"] = meta("og:image")
        out["sitio"] = meta("og:site_name") or urllib.parse.urlparse(url).netloc
    except Exception as e:
        print("Vista previa sin datos:", url[:60], type(e).__name__)
    return out


def vistas_previas(db):
    """Agrega vista previa a las novedades nuevas que tengan enlaces."""
    for dd in db.collection("novedades").stream():
        n = dd.to_dict()
        if n.get("previews") is not None:
            continue
        urls = [n["url"]] if n.get("url") else re.findall(r"https?://[^\s<]+", n.get("texto", ""))[:3]
        dd.reference.update({"previews": [og(u) for u in urls]})


def _db():
    import firebase_admin
    from firebase_admin import credentials, firestore
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(json.loads(os.environ["FIREBASE_CLAVE"])))
    return firestore.client()


def libros(db, tok):
    """Guarda el libro de ofertas real (IOL) de los activos que los alumnos están mirando."""
    from google.cloud.firestore_v1.base_query import FieldFilter
    ahora = int(time.time() * 1000)
    for dd in db.collection("libro_pedidos").where(filter=FieldFilter("t", ">", ahora - 180000)).stream():
        p = dd.to_dict()
        if ahora - p.get("t", 0) > 180000:   # hace más de 3 minutos que nadie lo mira
            continue
        s = p["s"]
        try:
            d = llamar(f"/broker/market-data/{urllib.parse.quote(s)}/depth",
                       {"market": "BCBA", "term": PLAZO}, token=tok)["data"]
        except Exception as e:
            print(f"Libro de {s} no disponible: {e}")
            continue
        nivel = lambda L: [{"p": x["price"], "c": x["size"]}
                           for x in sorted(L or [], key=lambda x: x.get("position", 0))[:6]]
        bids, asks = nivel(d.get("bids")), nivel(d.get("asks"))
        ref = db.collection("libros").document(s)
        if bids or asks:
            ref.set({"s": s, "t": ahora, "tl": ahora, "vacio": False, "bids": bids, "asks": asks})
        else:   # sin ofertas (mercado cerrado): se conserva el último libro real y se marca como vacío
            ref.set({"s": s, "t": ahora, "vacio": True}, merge=True)
        time.sleep(0.3)


def pulso():
    if not os.environ.get("FIREBASE_CLAVE") or not TOKEN.get("t"):
        return
    try:
        libros(_db(), TOKEN["t"])
    except Exception as e:
        print("Libros de ofertas con error:", e)


def dividendos(db, precios, hoy, usuarios):
    """Acredita los dividendos en dólares a quienes tenían el activo antes de la fecha de corte."""
    from firebase_admin import firestore
    try:
        cal = json.load(open("data/dividendos.json", encoding="utf-8"))
    except Exception as e:
        print("Sin calendario de dividendos:", e)
        return
    for d in [x for x in cal.get("dividendos", []) if x["pago"] <= hoy]:
        s = d["s"]
        ret = d.get("ret", cal.get("retencion_por_defecto", 0.30))
        corte = int(datetime.strptime(d["ex"], "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=-3))).timestamp() * 1000)
        for ud in usuarios:
            u = ud.to_dict()
            if d["id"] in (u.get("divs") or []):
                continue
            qty = 0
            for o in sorted(u.get("ops") or [], key=lambda x: x["t"]):
                if o["t"] < corte and o["s"] == s and o["l"] in ("C", "V"):
                    qty += o["c"] if o["l"] == "C" else -o["c"]
            if qty <= 0:
                continue
            uref = ud.reference

            @firestore.transactional
            def pagar(tx):
                cur = uref.get(transaction=tx).to_dict()
                if d["id"] in (cur.get("divs") or []):
                    return
                upd = {"divs": (cur.get("divs") or []) + [d["id"]]}
                if qty > 0:
                    neto = round(qty * d["usd"] * (1 - ret), 2)
                    upd["usd"] = round(cur.get("usd", 0) + neto, 2)
                    upd["ops"] = ((cur.get("ops") or []) + [{"t": int(time.time() * 1000), "s": s, "l": "D", "c": qty,
                                  "p": round(d["usd"] * (1 - ret), 4), "com": 0, "monto": neto, "m": "USD",
                                  "usd": d["usd"], "ret": ret}])[-1000:]
                tx.update(uref, upd)
            pagar(db.transaction())
            if qty > 0:
                print(f"Dividendo {d['id']} acreditado a {u.get('usuario')}: {qty} unidades.")


ULT = {"t": 0}


def evolucion(db, precios, usuarios):
    """Guarda el patrimonio de cada alumno (cada 5 minutos) en un documento por día, para el gráfico."""
    ahora = int(time.time() * 1000)
    if ahora - ULT["t"] < 270000:
        return
    from zoneinfo import ZoneInfo
    ba = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    mep = (tc(precios, "MEP") or {}).get("mid") or fxmed(precios)
    ccl = (tc(precios, "CCL") or {}).get("mid") or mep
    v = {}
    for dd in usuarios:
        u = dd.to_dict()
        val = u.get("cash", 0) + u.get("usd", 0) * mep + u.get("ccl", 0) * ccl
        for s, p in (u.get("pos") or {}).items():
            ult = (precios.get(s) or {}).get("ultimo")
            unit = ult * p.get("k", 1) if ult else (p["costo"] / p["c"] if p["c"] else 0)
            val += p["c"] * unit * {"USD": mep, "CCL": ccl}.get(p.get("m"), 1)
        v[dd.id] = round(val, 2)
    dia = ba.strftime("%Y-%m-%d")
    db.collection("evolucion_dia").document(dia).set({"d": dia, "h" + ba.strftime("%H%M"): v}, merge=True)
    ULT["t"] = ahora


BONOS = {}


def cargar_bonos():
    try:
        BONOS.clear()
        BONOS.update(json.load(open("data/bonos.json", encoding="utf-8"))["bonos"])
    except Exception as e:
        print("Sin calendario de bonos:", e)


def base_bono(s):
    if s in BONOS:
        return s
    return s[:-1] if s[:-1] in BONOS and s[-1:] in ("C", "D", "O") else None


def operable_s(s, d):
    if d.get("tipo") in ("bonos", "obligaciones_negociables"):
        return bool(base_bono(s)) and (d.get("ultimo") or 0) > 0
    return operable(d)


def pagos_bonos(db, hoy, usuarios):
    """Acredita rentas y amortizaciones (en dólares) a quienes tenían el bono antes de la fecha de pago."""
    from firebase_admin import firestore
    for b, info in BONOS.items():
        campo = "ccl" if info["pago"] == "ccl" else "usd"
        for fl in info["flujos"]:
            if fl["f"] > hoy:
                continue
            pid = f"{b}-{fl['f']}"
            corte = int(datetime.strptime(fl["f"], "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=-3))).timestamp() * 1000)
            for ud in usuarios:
                u = ud.to_dict()
                if pid in (u.get("divs") or []):
                    continue
                qty = {}
                for o in sorted(u.get("ops") or [], key=lambda x: x["t"]):
                    if o["t"] < corte and o["l"] in ("C", "V") and base_bono(o["s"]) == b:
                        qty[o["s"]] = qty.get(o["s"], 0) + (o["c"] if o["l"] == "C" else -o["c"])
                if not any(q > 0 for q in qty.values()):
                    continue
                uref = ud.reference

                @firestore.transactional
                def pagar(tx):
                    cur = uref.get(transaction=tx).to_dict()
                    if pid in (cur.get("divs") or []):
                        return
                    upd, total, nuevas = {"divs": (cur.get("divs") or []) + [pid]}, 0, []
                    ms = int(time.time() * 1000)
                    for s, q in qty.items():
                        if q <= 0:
                            continue
                        for det, pct in (("Renta de bono", fl["r"]), ("Amortización de bono", fl["a"])):
                            if pct > 0:
                                monto = round(q * pct / 100, 2)
                                total += monto
                                nuevas.append({"t": ms, "s": s, "l": "D", "c": q, "p": round(pct / 100, 6), "com": 0,
                                               "monto": monto, "m": "USD" if campo == "usd" else "CCL", "det": det})
                    if nuevas:
                        upd[campo] = round(cur.get(campo, 0) + total, 2)
                        upd["ops"] = ((cur.get("ops") or []) + nuevas)[-1000:]
                    tx.update(uref, upd)
                pagar(db.transaction())


LIMP = {"t": 0}


def limpiar(db):
    """Borra datos viejos (cada hora): evolución de más de 30 días y pedidos de libro de más de un día."""
    ahora = int(time.time() * 1000)
    if ahora - LIMP["t"] < 3600000:
        return
    LIMP["t"] = ahora
    from google.cloud.firestore_v1.base_query import FieldFilter
    for col, edad in (("evolucion", 30 * 86400000), ("libro_pedidos", 86400000)):
        for dd in db.collection(col).where(filter=FieldFilter("t", "<", ahora - edad)).limit(200).stream():
            dd.reference.delete()
    d0 = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    for dd in db.collection("evolucion_dia").where(filter=FieldFilter("d", "<", d0)).limit(50).stream():
        dd.reference.delete()


def motor(precios, actualizado=None):
    """Evalúa las órdenes pendientes con los precios reales. Solo corre en horario de mercado."""
    clave = os.environ.get("FIREBASE_CLAVE")
    if not clave:
        print("Motor de órdenes: falta el secreto FIREBASE_CLAVE, se omite.")
        return
    import firebase_admin
    from firebase_admin import credentials, firestore
    from zoneinfo import ZoneInfo
    from google.cloud.firestore_v1.base_query import FieldFilter
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(json.loads(clave)))
    db = firestore.client()
    cargar_bonos()
    try:
        vistas_previas(db)
    except Exception as e:
        print("Vistas previas con error:", e)
    ahora = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    hoy, mins = ahora.strftime("%Y-%m-%d"), ahora.hour * 60 + ahora.minute
    if actualizado:   # avisa a todas las páginas que hay precios nuevos
        db.collection("estado").document("precios").set({"actualizado": actualizado, "t": int(time.time() * 1000)})
    abierto = ahora.weekday() < 5 and 645 <= mins < 1080
    docs = sorted(db.collection("ordenes").where(filter=FieldFilter("estado", "==", "pendiente")).stream(),
                  key=lambda x: x.to_dict().get("t", 0))
    hechas = 0
    for dd in docs:
        o, oref = dd.to_dict(), dd.reference
        if o.get("vigencia") == "dia" and (o.get("dia", "") < hoy or (o.get("dia") == hoy and mins >= 1080)):
            oref.update({"estado": "vencida"})
            continue
        if o["s"] == "USD":
            if abierto:
                hechas += cambio(db, oref, o, precios)
            continue
        d = precios.get(o["s"])
        if not abierto or not d or not (d.get("ultimo") or 0) > 0:
            continue
        if not operable_s(o["s"], d):
            oref.update({"estado": "rechazada", "motivo": "Activo no habilitado para operar."})
            continue
        ult = d["ultimo"]
        compra, venta = d.get("compra") or ult, d.get("venta") or ult   # compra = oferta de compra; venta = oferta de venta
        c = o["lado"] == "C"
        ref = venta if c else compra
        if o["tipo"] == "stop_limite" and not o.get("activada"):
            if (c and ult >= o["stop"]) or (not c and ult <= o["stop"]):
                oref.update({"activada": True})
            else:
                continue
        if o["tipo"] != "mercado" and ((c and ref > o["limite"]) or (not c and ref < o["limite"])):
            continue
        hechas += ejecutar(db, oref, o, ref, TASA(d), mon_de(o["s"], d, precios), 0.01 if d.get("tipo") in ("bonos", "obligaciones_negociables") else 1)
    print(f"Motor de órdenes: {len(docs)} pendientes, {hechas} ejecutadas. Mercado {'abierto' if abierto else 'cerrado'}.")
    try:
        usuarios = list(db.collection("users").stream())   # una sola lectura por vuelta
        dividendos(db, precios, hoy, usuarios)
        pagos_bonos(db, hoy, usuarios)
        if abierto or (ahora.weekday() < 5 and 1080 <= mins < 1095):
            evolucion(db, precios, usuarios)
        limpiar(db)
    except Exception as e:
        print("Dividendos o evolución con error:", e)


def main():
    usuario, clave = os.environ.get("IOL_USUARIO"), os.environ.get("IOL_CLAVE")
    if not usuario or not clave:
        sys.exit("Faltan los secretos IOL_USUARIO e IOL_CLAVE en GitHub.")
    fin = time.time() + int(os.environ.get("MINUTOS") or 0) * 60
    ciclo = 0
    while True:
        ciclo += 1
        inicio = time.time()
        try:
            salida = traer(usuario, clave, ciclo == 1)
            publicar(salida)
            try:
                motor(salida["precios"], salida["actualizado"])
            except Exception as e:
                print("Motor de órdenes con error:", e)
            print(f"Ciclo {ciclo} OK")
        except Exception as e:
            print(f"Ciclo {ciclo} con error: {e}")
            if ciclo == 1:
                sys.exit("Falló el primer intento. Puede ser usuario/clave, la verificación en dos pasos "
                         "o un bloqueo de IOL a los servidores de GitHub.")
        if time.time() + INTERVALO > fin:
            break
        while time.time() < inicio + INTERVALO:   # el ciclo completo dura un minuto, contando el tiempo de trabajo
            time.sleep(max(1, min(30, inicio + INTERVALO - time.time())))
            pulso()


main()
