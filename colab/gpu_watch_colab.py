# %% [markdown]
# # GPU Watch - Capteur, calcul des coûts et analyste IA (Google Colab)
#
# À exécuter dans un carnet Google Colab avec un GPU (Exécution > Modifier le type d'exécution > GPU T4).
# Chaque bloc `# %%` correspond à une cellule du carnet, à exécuter dans l'ordre.
#
# Les secrets ne sont jamais écrits dans le code : ils sont lus depuis les "Secrets" de Colab
# (icône de clé dans la barre de gauche) :
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
                print("Refus du collecteur :", r.status_code, r.text)
        except Exception as e:
            print("Erreur capteur :", e)
        time.sleep(5)


threading.Thread(target=boucle_capteur, daemon=True).start()
print("Capteur démarré et branché sur AWS")

# %% Cellule 2 - Générer une charge de test (90 secondes de calcul intensif)
import torch

a = torch.randn(8000, 8000, device="cuda")
b = torch.randn(8000, 8000, device="cuda")

print("Le GPU travaille pendant 90 secondes...")
fin = time.time() + 90
while time.time() < fin:
    c = a @ b
    torch.cuda.synchronize()
print("Terminé, le GPU se repose")

# %% Cellule 3 - Premier calcul du gaspillage (sur les mesures collectées dans Colab)
PRIX_HEURE = 0.526     # prix d'une g4dn.xlarge, en dollars par heure
INTERVALLE = 5         # une mesure toutes les 5 secondes
SEUIL_INACTIF = 10     # sous 10 % d'utilisation, le GPU ne fait rien d'utile
HEURES_PAR_MOIS = 730
PRIX_SPOT = 0.25       # prix Spot approximatif d'une g4dn.xlarge

cout_par_mesure = PRIX_HEURE * INTERVALLE / 3600
inactives = [m for m in mesures if m["utilisation"] < SEUIL_INACTIF]
part_inactive = len(inactives) / len(mesures)

cout_mois = HEURES_PAR_MOIS * PRIX_HEURE
cout_arret_auto = HEURES_PAR_MOIS * (1 - part_inactive) * PRIX_HEURE
cout_arret_auto_spot = HEURES_PAR_MOIS * (1 - part_inactive) * PRIX_SPOT

print(f"Mesures collectées : {len(mesures)} (soit {len(mesures) * INTERVALLE / 60:.1f} minutes)")
print(f"Dont inactives     : {len(inactives)} ({part_inactive:.0%})")
print(f"Argent gaspillé    : {len(inactives) * cout_par_mesure:.4f} $")
print()
print(f"Coût mensuel actuel          : {cout_mois:7.2f} $")
print(f"  dont gaspillé              : {cout_mois * part_inactive:7.2f} $")
print(f"Avec arrêt automatique       : {cout_arret_auto:7.2f} $")
print(f"Avec arrêt auto + Spot       : {cout_arret_auto_spot:7.2f} $")

# %% Cellule 4 - Charger l'analyste IA sur le GPU
from transformers import AutoModelForCausalLM, AutoTokenizer

NOM_MODELE = "Qwen/Qwen2.5-3B-Instruct"

tokenizer = AutoTokenizer.from_pretrained(NOM_MODELE)
modele = AutoModelForCausalLM.from_pretrained(
    NOM_MODELE,
    torch_dtype=torch.float16,  # demi-précision : deux fois moins de mémoire
    device_map="cuda",
)
print("Analyste IA chargé sur", torch.cuda.get_device_name(0))

# %% Cellule 5 - Préparer les faits pour l'analyste (Python calcule, l'IA raconte)
import statistics
from zoneinfo import ZoneInfo


def recuperer_mesures():
    r = requests.get(URL_COLLECTEUR, params={"donnees": "1"}, timeout=10)
    return r.json()


def heure_locale(horodatage):
    return datetime.datetime.fromisoformat(horodatage).astimezone(ZoneInfo("Europe/Paris"))


def calculer_couts(mesures):
    cout_par_mesure = PRIX_HEURE * INTERVALLE / 3600
    inactives = [m for m in mesures if m["utilisation"] < SEUIL_INACTIF]
    part_inactive = len(inactives) / len(mesures)
    cout_mois = HEURES_PAR_MOIS * PRIX_HEURE
    return {
        "minutes": len(mesures) * INTERVALLE / 60,
        "minutes_inactives": len(inactives) * INTERVALLE / 60,
        "part_inactive": part_inactive,
        "cout_periode": len(mesures) * cout_par_mesure,
        "gaspi_periode": len(inactives) * cout_par_mesure,
        "cout_mois": cout_mois,
        "gaspi_mois": cout_mois * part_inactive,
        "mois_arret_auto": HEURES_PAR_MOIS * (1 - part_inactive) * PRIX_HEURE,
        "mois_arret_auto_spot": HEURES_PAR_MOIS * (1 - part_inactive) * PRIX_SPOT,
    }


def periodes_actives(par_minute):
    """Regroupe les minutes actives qui se suivent : 16:02, 16:03 -> 'de 16:02 à 16:03'."""
    periodes = []
    for minute, groupe in par_minute.items():
        actif = statistics.mean(g["utilisation"] for g in groupe) >= SEUIL_INACTIF
        if not actif:
            continue
        h, mn = map(int, minute.split(":"))
        t = h * 60 + mn                      # l'heure en minutes depuis minuit
        if periodes and periodes[-1][1] == t - 1:
            periodes[-1][1] = t              # minute suivante : on prolonge la période
        else:
            periodes.append([t, t])          # sinon : nouvelle période
    return [f"{d // 60:02d}:{d % 60:02d} à {f // 60:02d}:{f % 60:02d}" for d, f in periodes]


def resumer(mesures):
    def serie(cle):
        return [m[cle] for m in mesures if m.get(cle) is not None]

    temp, mem = serie("temperature"), serie("memoire_utilisee")
    mem_totale = mesures[-1]["memoire_totale"]
    c = calculer_couts(mesures)

    par_minute = {}
    for m in mesures:
        minute = heure_locale(m["horodatage"]).strftime("%H:%M")
        par_minute.setdefault(minute, []).append(m)
    periodes = periodes_actives(par_minute)
    debut, fin = list(par_minute)[0], list(par_minute)[-1]

    lignes = [
        f"GPU : {mesures[-1]['gpu']}",
        f"Fenêtre observée : de {debut} à {fin}",
        f"Périodes d'activité : {', '.join(periodes) if periodes else 'aucune, le GPU n a jamais travaillé'}",
        f"Temps inactif : {c['minutes_inactives']:.0f} minutes sur {c['minutes']:.0f} minutes observées",
        f"Température : max {max(temp):.0f} °C (seuil d'alerte : 80 °C, "
        f"{'DÉPASSÉ' if max(temp) > 80 else 'jamais dépassé'})",
        f"Mémoire : max {max(mem) / 1024:.1f} Go sur {mem_totale / 1024:.1f} Go",
        "",
        f"COÛTS (tarif g4dn.xlarge : {PRIX_HEURE} $/h, Spot : {PRIX_SPOT} $/h)",
        f"- Sur la fenêtre observée : {c['cout_periode']:.3f} $ dépensés au total, "
        f"dont {c['gaspi_periode']:.3f} $ payés pour rien",
        f"- Projection mensuelle : {c['cout_mois']:.0f} $ dépensés, dont {c['gaspi_mois']:.0f} $ gaspillés",
        "",
        "ÉCONOMIES POSSIBLES PAR MOIS",
        f"- Option 1, arrêt automatique seul : la facture passe à {c['mois_arret_auto']:.0f} $, "
        f"économie de {c['cout_mois'] - c['mois_arret_auto']:.0f} $",
        f"- Option 2, arrêt automatique + Spot : la facture passe à {c['mois_arret_auto_spot']:.0f} $, "
        f"économie de {c['cout_mois'] - c['mois_arret_auto_spot']:.0f} $",
        "",
        "INFORMATIONS SUR LE SPOT : une instance Spot coûte environ deux fois moins cher, "
        "mais AWS peut la reprendre à tout moment avec 2 minutes de préavis. "
        "Elle convient aux calculs qu'on peut interrompre et relancer (entraînement avec sauvegardes régulières, "
        "traitements par lots). Elle ne convient pas à un service qui doit répondre en permanence.",
    ]
    return "\n".join(lignes)


print(resumer(recuperer_mesures()))

# %% Cellule 6 - L'analyste FinOps rédige son rapport
def analyser():
    mesures = recuperer_mesures()
    if not mesures:
        print("Aucune mesure récente : vérifie que le capteur tourne.")
        return None

    resume = resumer(mesures)
    messages = [
        {"role": "system", "content":
            "Tu es un ingénieur FinOps spécialisé dans les GPU cloud : "
            "ton travail est de réduire les dépenses inutiles. "
            "Tu écris uniquement en français, clairement et simplement. "
            "Règles strictes : tous les chiffres, heures et durées sont déjà calculés. "
            "Tu les reprends tels quels, sans jamais calculer, convertir ou comparer quoi que ce soit toi-même. "
            "Pour la température, reprends exactement la mention 'DÉPASSÉ' ou 'jamais dépassé'."},
        {"role": "user", "content":
            f"Voici les données de mon GPU :\n\n{resume}\n\n"
            "Rédige un rapport en exactement 3 paragraphes, séparés par une ligne vide :\n"
            "Activité : les périodes d'activité et le temps inactif, repris des données.\n"
            "Coûts : la dépense de la fenêtre observée, la part gaspillée, et la projection mensuelle.\n"
            "Recommandations : présente l'option 1 puis l'option 2 avec leurs montants exacts, "
            "puis explique avec les informations sur le Spot quand l'option 2 est adaptée.\n"
            "N'écris pas de titres en majuscules ni en gras. 120 mots maximum."},
    ]

    texte_prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    entrees = tokenizer(texte_prompt, return_tensors="pt").to("cuda")
    sortie = modele.generate(**entrees, max_new_tokens=600, do_sample=False)
    diagnostic = tokenizer.decode(sortie[0][entrees["input_ids"].shape[1]:], skip_special_tokens=True)

    print("DIAGNOSTIC DE L'ANALYSTE IA")
    print("=" * 40)
    print(diagnostic)
    return diagnostic


diagnostic = analyser()

# %% Cellule 7 - Publier le rapport sur le tableau de bord
def publier_analyse(texte):
    analyse = {
        "type": "analyse",
        "gpu": torch.cuda.get_device_name(0),
        "horodatage": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "texte": texte,
    }
    r = requests.post(URL_COLLECTEUR, json=analyse, headers={"x-jeton": JETON}, timeout=10)
    if r.status_code == 200:
        print("Analyse publiée sur le tableau de bord")
    else:
        print(f"Erreur {r.status_code} : {r.text}")


if diagnostic:
    publier_analyse(diagnostic)

# %% Cellule 8 - Mode autonome : un rapport toutes les 5 minutes (arrêter avec le bouton stop)
print("Mode autonome : un rapport toutes les 5 minutes")
while True:
    d = analyser()
    if d:
        publier_analyse(d)
    time.sleep(300)
