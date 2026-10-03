"""
GPU Watch - Fonction AWS Lambda (collecteur + API + tableau de bord)

Rôles :
  - POST  /                 : reçoit une mesure GPU ou une analyse IA (protégé par jeton)
  - GET   /                 : renvoie la page du tableau de bord
  - GET   /?donnees=1       : renvoie les mesures des 15 dernières minutes
  - GET   /?analyse=1       : renvoie la dernière analyse IA

Variables d'environnement requises :
  - TABLE : nom de la table DynamoDB (clé de partition "gpu", clé de tri "horodatage")
  - JETON : jeton secret partagé avec le capteur
"""

import json, os, base64, datetime
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key

table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
JETON = os.environ["JETON"]
cloudwatch = boto3.client("cloudwatch")

# ---------- La page du tableau de bord ----------
PAGE_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GPU Watch</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root { --fond:#0b0f19; --carte:#121829; --bord:#1f2a44; --texte:#e2e8f0; --doux:#94a3b8; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--fond); color:var(--texte); font-family:system-ui, sans-serif; padding:24px; }
  header { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:24px; }
  h1 { margin:0; font-size:1.7rem; letter-spacing:.5px; }
  .sous { color:var(--doux); font-size:.9rem; margin-top:4px; }
  .statut { padding:6px 14px; border-radius:999px; font-size:.85rem; border:1px solid var(--bord); }
  .en-ligne { color:#4ade80; border-color:#166534; }
  .hors-ligne { color:#f87171; border-color:#7f1d1d; }

  /* Mise en page : contenu à gauche, analyse IA à droite */
  .mise-en-page { display:grid; grid-template-columns:minmax(0, 1fr) 400px; gap:16px; align-items:start; }
  .colonne-principale { min-width:0; }

  .grille-jauges { display:grid; grid-template-columns:repeat(auto-fit, minmax(210px, 1fr)); gap:16px; margin-bottom:16px; }
  .grille-courbes { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:16px; }
  .carte { background:var(--carte); border:1px solid var(--bord); border-radius:16px; padding:20px; }
  .jauge { display:flex; align-items:center; gap:18px; }
  .jauge svg { width:90px; height:90px; transform:rotate(-90deg); flex-shrink:0; }
  .fond-anneau { fill:none; stroke:#1f2a44; stroke-width:10; }
  .anneau { fill:none; stroke-width:10; stroke-linecap:round; transition:stroke-dashoffset .8s ease; }
  .valeur { font-size:1.7rem; font-weight:600; }
  .libelle { color:var(--doux); font-size:.85rem; }
  .zone-graph { position:relative; height:190px; margin-top:10px; }

  /* La colonne d'analyse IA */
  .analyse { position:sticky; top:24px; max-height:calc(100vh - 48px); overflow-y:auto;
             border-color:#3b2f6b; background:linear-gradient(160deg, #151a2e, #1a1433);
             scrollbar-color:#3b2f6b transparent; scrollbar-width:thin; }
  .analyse::-webkit-scrollbar { width:8px; }
  .analyse::-webkit-scrollbar-track { background:transparent; }
  .analyse::-webkit-scrollbar-thumb { background:#3b2f6b; border-radius:8px; }
  .analyse-titre { color:#c4b5fd; font-weight:600; font-size:1rem; }
  .analyse-heure { color:var(--doux); font-size:.8rem; margin-top:4px; }
  .analyse-texte { white-space:pre-wrap; line-height:1.6; margin-top:14px; font-size:.95rem; }

  /* Bandeau des coûts */
  .titre-section { color:var(--doux); font-size:.75rem; text-transform:uppercase; letter-spacing:1.5px; margin:0 0 10px 4px; }
  .grille-couts { display:grid; grid-template-columns:repeat(auto-fit, minmax(210px, 1fr)); gap:16px; margin-bottom:24px; }
  .cout .montant { font-size:1.9rem; font-weight:700; margin-top:6px; }
  .cout .detail { color:var(--doux); font-size:.8rem; margin-top:6px; }
  .cout.gaspi { border-color:#7f1d1d; background:linear-gradient(160deg, #1c1420, #121829); }
  .cout.gaspi .montant { color:#f87171; }
  .cout.eco { border-color:#14532d; background:linear-gradient(160deg, #0f1f1a, #121829); }
  .cout.eco .montant { color:#4ade80; }

  /* Petits écrans : l'analyse passe en dessous */
  @media (max-width: 1100px) {
    .mise-en-page { grid-template-columns:1fr; }
    .grille-courbes { grid-template-columns:1fr; }
    .analyse { position:static; max-height:none; }
  }
</style>
</head>
<body>
<header>
  <div>
    <h1>GPU Watch</h1>
    <div class="sous" id="nom-gpu">En attente de données…</div>
  </div>
  <div class="statut hors-ligne" id="statut">● Hors ligne</div>
</header>

<div class="mise-en-page">
  <div class="colonne-principale">

    <div class="titre-section">Coûts · 15 dernières minutes</div>
    <section class="grille-couts">
      <div class="carte cout">
        <div class="libelle">Coût de la période</div>
        <div class="montant" id="cout-total">–</div>
        <div class="detail" id="cout-total-detail">–</div>
      </div>
      <div class="carte cout gaspi">
        <div class="libelle">Argent gaspillé (GPU inactif)</div>
        <div class="montant" id="cout-gaspi">–</div>
        <div class="detail" id="cout-gaspi-detail">–</div>
      </div>
      <div class="carte cout gaspi">
        <div class="libelle">Gaspillage projeté sur un mois</div>
        <div class="montant" id="cout-mois">–</div>
        <div class="detail">si ce rythme se maintient 24 h/24</div>
      </div>
      <div class="carte cout eco">
        <div class="libelle">Économie possible par mois</div>
        <div class="montant" id="cout-eco">–</div>
        <div class="detail">arrêt automatique + instance Spot</div>
      </div>
    </section>

    <div class="titre-section">Métriques en direct</div>
    <section class="grille-jauges" id="jauges"></section>
    <section class="grille-courbes" id="courbes"></section>
  </div>

  <aside class="carte analyse">
    <div class="analyse-titre">Analyse IA</div>
    <div class="analyse-heure" id="analyse-heure">Aucune analyse pour l'instant</div>
    <div class="analyse-texte" id="analyse-texte">Lance l'analyste depuis Colab pour voir son diagnostic ici.</div>
  </aside>
</div>

<script>
  const METRIQUES = [
    { cle:"utilisation",      titre:"Utilisation", unite:"%",  couleur:"#22d3ee", max:() => 100 },
    { cle:"memoire_utilisee", titre:"Mémoire",     unite:"Go", couleur:"#a78bfa", conv:v => v/1024, max:d => d.memoire_totale/1024 },
    { cle:"temperature",      titre:"Température", unite:"°C", couleur:"#fb923c", max:() => 90 },
    { cle:"puissance",        titre:"Puissance",   unite:"W",  couleur:"#facc15", max:() => 70 },
  ];
  const CIRC = 2 * Math.PI * 40;
  const graphiques = {};

  for (const m of METRIQUES) {
    document.getElementById("jauges").insertAdjacentHTML("beforeend", `
      <div class="carte jauge">
        <svg viewBox="0 0 100 100">
          <circle class="fond-anneau" cx="50" cy="50" r="40"/>
          <circle class="anneau" id="anneau-${m.cle}" cx="50" cy="50" r="40"
                  stroke="${m.couleur}" stroke-dasharray="${CIRC}" stroke-dashoffset="${CIRC}"/>
        </svg>
        <div><div class="valeur" id="val-${m.cle}">–</div><div class="libelle">${m.titre}</div></div>
      </div>`);
    document.getElementById("courbes").insertAdjacentHTML("beforeend", `
      <div class="carte">
        <div class="libelle">${m.titre} (${m.unite}) · 15 dernières minutes</div>
        <div class="zone-graph"><canvas id="graph-${m.cle}"></canvas></div>
      </div>`);
    graphiques[m.cle] = new Chart(document.getElementById("graph-" + m.cle), {
      type: "line",
      data: { labels: [], datasets: [{ data: [], borderColor: m.couleur, backgroundColor: m.couleur + "22",
              fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#64748b", maxTicksLimit: 6 }, grid: { color: "#1f2a44" } },
          y: { beginAtZero: true, ticks: { color: "#64748b" }, grid: { color: "#1f2a44" } }
        }
      }
    });
  }

  // ---------- Calcul des coûts (même logique que dans Colab) ----------
  const PRIX_HEURE = 0.526;      // g4dn.xlarge à la demande, en $/h
  const PRIX_SPOT = 0.25;        // g4dn.xlarge en Spot, en $/h
  const INTERVALLE = 5;          // une mesure toutes les 5 secondes
  const SEUIL_INACTIF = 10;      // sous 10 %, le GPU est inactif
  const HEURES_PAR_MOIS = 730;

  // Affiche un montant : 3 décimales sous 1 $, sinon 2, avec une virgule
  function dollars(valeur) {
    return valeur.toFixed(valeur < 1 ? 3 : 2).replace(".", ",") + " $";
  }

  function afficherCouts(mesures) {
    // 1. Coût d'une mesure (5 secondes de location)
    const coutParMesure = PRIX_HEURE * INTERVALLE / 3600;

    // 2. Les mesures inactives
    const inactives = mesures.filter(m => m.utilisation < SEUIL_INACTIF);

    // 3. Coûts sur la période
    const coutTotal = mesures.length * coutParMesure;
    const coutGaspille = inactives.length * coutParMesure;
    const partInactive = inactives.length / mesures.length;

    // 4. Projection sur un mois
    const coutMois = HEURES_PAR_MOIS * PRIX_HEURE;
    const gaspiMois = coutMois * partInactive;
    const coutArretAutoSpot = HEURES_PAR_MOIS * (1 - partInactive) * PRIX_SPOT;
    const economie = coutMois - coutArretAutoSpot;

    // 5. Remplir les cartes
    const minutes = mesures.length * INTERVALLE / 60;
    document.getElementById("cout-total").textContent = dollars(coutTotal);
    document.getElementById("cout-total-detail").textContent =
      Math.round(minutes) + " min au tarif g4dn.xlarge (" + PRIX_HEURE + " $/h)";
    document.getElementById("cout-gaspi").textContent = dollars(coutGaspille);
    document.getElementById("cout-gaspi-detail").textContent =
      Math.round(partInactive * 100) + " % du temps sous " + SEUIL_INACTIF + " % d'utilisation";
    document.getElementById("cout-mois").textContent = dollars(gaspiMois);
    document.getElementById("cout-eco").textContent = dollars(economie);
  }

  async function actualiser() {
    try {
      const rep = await fetch("?donnees=1");
      const mesures = await rep.json();
      const statut = document.getElementById("statut");
      if (!mesures.length) {
        statut.textContent = "● Aucune donnée récente";
        statut.className = "statut hors-ligne";
        return;
      }
      const derniere = mesures[mesures.length - 1];
      document.getElementById("nom-gpu").textContent =
        derniere.gpu + " · " + mesures.length + " mesures sur 15 min";

      const age = (Date.now() - new Date(derniere.horodatage)) / 1000;
      if (age < 20) {
        statut.textContent = "● En ligne";
        statut.className = "statut en-ligne";
      } else {
        statut.textContent = "● Silencieux depuis " +
          (age < 60 ? Math.round(age) + " s" : Math.round(age / 60) + " min");
        statut.className = "statut hors-ligne";
      }

      afficherCouts(mesures);

      const heures = mesures.map(x => new Date(x.horodatage).toLocaleTimeString("fr-FR"));
      for (const m of METRIQUES) {
        const conv = m.conv || (v => v);
        const valeurs = mesures.map(x => x[m.cle] == null ? null : conv(x[m.cle]));
        const v = valeurs[valeurs.length - 1];
        const ratio = v == null ? 0 : Math.min(v / m.max(derniere), 1);
        document.getElementById("val-" + m.cle).textContent =
          v == null ? "–" : (Math.round(v * 10) / 10) + " " + m.unite;
        document.getElementById("anneau-" + m.cle).style.strokeDashoffset = CIRC * (1 - ratio);
        graphiques[m.cle].data.labels = heures;
        graphiques[m.cle].data.datasets[0].data = valeurs;
        graphiques[m.cle].update("none");
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function actualiserAnalyse() {
    try {
      const rep = await fetch("?analyse=1");
      const a = await rep.json();
      if (!a.texte) return;
      const heure = new Date(a.horodatage).toLocaleTimeString("fr-FR");
      document.getElementById("analyse-heure").textContent = "Dernière analyse à " + heure;
      document.getElementById("analyse-texte").textContent = a.texte.replaceAll("**", "");
    } catch (e) {
      console.error(e);
    }
  }

  actualiser();
  actualiserAnalyse();
  setInterval(actualiser, 5000);
  setInterval(actualiserAnalyse, 15000);
</script>
</body>
</html>"""


def reponse(code, contenu, type_contenu="application/json"):
    corps = contenu if isinstance(contenu, str) else json.dumps(contenu, ensure_ascii=False, default=float)
    return {"statusCode": code, "headers": {"Content-Type": type_contenu}, "body": corps}


def lambda_handler(event, context):
    methode = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    params = event.get("queryStringParameters") or {}

    # ----- GET : le tableau de bord -----
    if methode == "GET":
        gpu = params.get("gpu", "Tesla T4")

        # Les mesures des 15 dernières minutes
        if "donnees" in params:
            depuis = (datetime.datetime.now(datetime.timezone.utc)
                      - datetime.timedelta(minutes=15)).isoformat()
            resultat = table.query(
                KeyConditionExpression=Key("gpu").eq(gpu) & Key("horodatage").gt(depuis)
            )
            return reponse(200, resultat["Items"])

        # La dernière analyse de l'IA
        if "analyse" in params:
            resultat = table.query(
                KeyConditionExpression=Key("gpu").eq("analyse:" + gpu),
                ScanIndexForward=False,   # du plus récent au plus ancien
                Limit=1,
            )
            items = resultat["Items"]
            return reponse(200, items[0] if items else {})

        # Sinon : la page web
        return reponse(200, PAGE_HTML, "text/html; charset=utf-8")

    # ----- POST : réception (protégée par le jeton) -----
    entetes = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if entetes.get("x-jeton") != JETON:
        return reponse(401, {"erreur": "Jeton invalide"})

    corps = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        corps = base64.b64decode(corps).decode("utf-8")
    mesure = json.loads(corps, parse_float=Decimal)

    if "gpu" not in mesure or "horodatage" not in mesure:
        return reponse(400, {"erreur": "Données incomplètes"})

    # Cas 1 : un diagnostic de l'analyste IA
    if mesure.get("type") == "analyse":
        mesure["gpu"] = "analyse:" + mesure["gpu"]   # rangé à part des mesures
        table.put_item(Item=mesure)
        return reponse(200, {"ok": True})

    # Cas 2 : une mesure du capteur
    table.put_item(Item=mesure)

    donnees_cw = []
    for cle, nom_metrique, unite in [
        ("utilisation", "Utilisation", "Percent"),
        ("temperature", "Temperature", "None"),
    ]:
        if mesure.get(cle) is not None:
            donnees_cw.append({
                "MetricName": nom_metrique,
                "Dimensions": [{"Name": "GPU", "Value": mesure["gpu"]}],
                "Value": float(mesure[cle]),
                "Unit": unite,
            })
    if donnees_cw:
        cloudwatch.put_metric_data(Namespace="GPUWatch", MetricData=donnees_cw)

    return reponse(200, {"ok": True})
