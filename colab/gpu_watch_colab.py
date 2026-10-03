# %% [markdown]
# # GPU Watch - Capteur et analyste IA (Google Colab)
#
# À exécuter dans un carnet Google Colab avec un GPU (Exécution > Modifier le type d'exécution > GPU T4).
# Chaque bloc `# %%` correspond à une cellule du carnet.
#
# Les secrets ne sont jamais écrits dans le code : ils sont lus depuis les "Secrets" de Colab
# (icône 🔑 dans la barre de gauche) :
#   - GPU_WATCH_URL   : l'URL de la fonction Lambda
#   - GPU_WATCH_JETON : le jeton secret (le même que la variable JETON de la Lambda)

# %% Cellule 1 - Le capteur
import subprocess, threading, time, datetime, os, requests

try:
    from google.colab import userdata
    URL_COLLECTEUR = userdata.get("GPU_WATCH_URL")
    JETON = userdata.get("GPU_WATCH_JETON")
except ImportError:  # exécution hors Colab
    URL_COLLECTEUR = os.environ["GPU_WATCH_URL"]
    JETON = os.environ["GPU_WATCH_JETON"]

INTERVALLE = 5  # secondes entre deux mesures
mesures = []


def nombre(texte):
    try:
        return float(texte)
    except ValueError:
        return None  # la carte ne fournit pas cette information


def lire_gpu():
    sortie = subprocess.check_output([
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]).decode().strip()
    nom, util, mem_used, mem_total, temp, power = [x.strip() for x in sortie.split(",")]
    return {
        "horodatage": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "gpu": nom,
        "utilisation": nombre(util),            # %
        "memoire_utilisee": nombre(mem_used),   # Mo
        "memoire_totale": nombre(mem_total),    # Mo
        "temperature": nombre(temp),            # °C
        "puissance": nombre(power),             # W
    }


def boucle_capteur():
    while True:
        try:
            m = lire_gpu()
            mesures.append(m)
            r = requests.post(URL_COLLECTEUR, json=m, headers={"x-jeton": JETON}, timeout=5)
            if r.status_code != 200:
                print("⚠️ Refus du collecteur :", r.status_code, r.text)
        except Exception as e:
            print("Erreur capteur :", e)
        time.sleep(INTERVALLE)


threading.Thread(target=boucle_capteur, daemon=True).start()
print(" Capteur démarré et branché sur AWS !")

# %% Cellule 2 - Générer une charge de test (90 secondes de calcul intensif)
import torch

a = torch.randn(8000, 8000, device="cuda")
b = torch.randn(8000, 8000, device="cuda")

print(" Le GPU travaille pendant 90 secondes...")
fin = time.time() + 90
while time.time() < fin:
    c = a @ b
    torch.cuda.synchronize()
print(" Terminé, le GPU se repose")

# %% Cellule 3 - Charger l'analyste IA sur le GPU
from transformers import AutoModelForCausalLM, AutoTokenizer

NOM_MODELE = "Qwen/Qwen2.5-3B-Instruct"

tokenizer = AutoTokenizer.from_pretrained(NOM_MODELE)
modele = AutoModelForCausalLM.from_pretrained(
    NOM_MODELE,
    torch_dtype=torch.float16,  # demi-précision : deux fois moins de mémoire
    device_map="cuda",
)
print(" Analyste IA chargé sur", torch.cuda.get_device_name(0))

# %% Cellule 4 - Préparer les données pour l'analyste
import statistics
from zoneinfo import ZoneInfo

SEUIL_TEMPERATURE = 80  # °C, identique à l'alarme CloudWatch


def recuperer_mesures():
    r = requests.get(URL_COLLECTEUR, params={"donnees": "1"}, timeout=10)
    return r.json()


def heure_locale(horodatage):
    return datetime.datetime.fromisoformat(horodatage).astimezone(ZoneInfo("Europe/Paris"))


def resumer(mesures):
    """Calcule les statistiques en Python : l'IA reçoit des chiffres déjà prêts."""
    def serie(cle):
        return [m[cle] for m in mesures if m.get(cle) is not None]

    util, temp = serie("utilisation"), serie("temperature")
    mem, puiss = serie("memoire_utilisee"), serie("puissance")
    mem_totale = mesures[-1]["memoire_totale"]
    actif = sum(1 for u in util if u >= 10)

    depassements = [heure_locale(m["horodatage"]).strftime("%H:%M")
                    for m in mesures if (m.get("temperature") or 0) > SEUIL_TEMPERATURE]
    if depassements:
        texte_seuil = (f"OUI, seuil de {SEUIL_TEMPERATURE} °C dépassé "
                       f"({len(depassements)} mesures, entre {depassements[0]} et {depassements[-1]})")
    else:
        texte_seuil = f"NON, le seuil de {SEUIL_TEMPERATURE} °C n'a jamais été dépassé"

    lignes = [
        f"GPU : {mesures[-1]['gpu']}",
        f"Période : {len(mesures)} mesures, une toutes les {INTERVALLE} secondes",
        f"Utilisation : moyenne {statistics.mean(util):.0f} %, max {max(util):.0f} %, "
        f"actif {100 * actif / len(util):.0f} % du temps",
        f"Température : min {min(temp):.0f} °C, moyenne {statistics.mean(temp):.0f} °C, max {max(temp):.0f} °C",
        f"Dépassement du seuil de température : {texte_seuil}",
        f"Mémoire : max {max(mem) / 1024:.1f} Go sur {mem_totale / 1024:.1f} Go",
        f"Puissance : moyenne {statistics.mean(puiss):.0f} W, max {max(puiss):.0f} W (limite de la carte : 70 W)",
        "",
        "Chronologie minute par minute (heure de Paris) :",
    ]

    par_minute = {}
    for m in mesures:
        minute = heure_locale(m["horodatage"]).strftime("%H:%M")
        par_minute.setdefault(minute, []).append(m)
    for minute, groupe in par_minute.items():
        u = statistics.mean(g["utilisation"] for g in groupe)
        t = max(g["temperature"] for g in groupe)
        go = max(g["memoire_utilisee"] for g in groupe) / 1024
        lignes.append(f"{minute} : utilisation {u:.0f} %, température max {t:.0f} °C, mémoire {go:.1f} Go")

    return "\n".join(lignes)


print(resumer(recuperer_mesures()))

# %% Cellule 5 - L'analyste rend son diagnostic
def analyser():
    mesures = recuperer_mesures()
    if not mesures:
        print("Aucune mesure récente : vérifie que le capteur tourne.")
        return None

    resume = resumer(mesures)
    messages = [
        {"role": "system", "content":
            "Tu es un ingénieur expert en exploitation de GPU dans un datacenter. "
            "Tu analyses des métriques et tu expliques en français, clairement et simplement, à un débutant. "
            "Tu ne parles que de ce que montrent les chiffres, sans rien inventer. "
            "Pour les dépassements de seuil, reprends exactement la ligne 'Dépassement du seuil'."},
        {"role": "user", "content":
            f"Voici les métriques de mon GPU sur les 15 dernières minutes :\n\n{resume}\n\n"
            "Fais un diagnostic en 4 parties courtes :\n"
            "1) Ce qui s'est passé, avec les heures\n"
            "2) L'état de santé du GPU\n"
            "3) Les points d'attention\n"
            "4) Une recommandation concrète\n"
            "Sois concis : 150 mots maximum au total."},
    ]

    texte_prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    entrees = tokenizer(texte_prompt, return_tensors="pt").to("cuda")
    sortie = modele.generate(**entrees, max_new_tokens=600, do_sample=False)
    diagnostic = tokenizer.decode(sortie[0][entrees["input_ids"].shape[1]:], skip_special_tokens=True)

    print(" DIAGNOSTIC DE L'ANALYSTE IA")
    print("=" * 40)
    print(diagnostic)
    return diagnostic


# %% Cellule 6 - Publier le diagnostic sur le tableau de bord
def publier_analyse(texte):
    analyse = {
        "type": "analyse",
        "gpu": torch.cuda.get_device_name(0),
        "horodatage": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "texte": texte,
    }
    r = requests.post(URL_COLLECTEUR, json=analyse, headers={"x-jeton": JETON}, timeout=10)
    if r.status_code == 200:
        print(" Analyse publiée sur le tableau de bord !")
    else:
        print(f" Erreur {r.status_code} : {r.text}")


diagnostic = analyser()
if diagnostic:
    publier_analyse(diagnostic)

# %% Cellule 7 - Mode autonome : une analyse toutes les 5 minutes (arrêter avec ■)
print("🤖 Mode autonome : une analyse toutes les 5 minutes")
while True:
    d = analyser()
    if d:
        publier_analyse(d)
    time.sleep(300)
