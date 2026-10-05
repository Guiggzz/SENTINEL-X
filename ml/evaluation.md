# SENTINEL-X - Evaluation du modele de maintenance predictive

Genere le 2026-10-05T12:56:40+00:00 par `ml/train.py` (graine 42).

## Modele

- Isolation Forest scikit-learn, 300 arbres, contamination 0.005
- Entrainement : 1004 fenetres reelles (sentinel-node-01, 2026-10-05T09:40:30.622191+00:00 -> 2026-10-05T12:14:02.134124+00:00 UTC) + 4016 fenetres augmentees (bruit MQ-2 +-10 ADC, DHT22 +-0.5 C / +-2 %RH, scenarios 'personne proche')
- Features (8) : gas_dev_rel, gas_slope_30s, gas_std_30s, gas_slope_2m, temp_slope_2m, temp_dev, hum_slope_2m, gas_temp_corr (projection directionnelle : seules hausses gaz/temp, chutes d'humidite, corr. positive)
- Calibration du score (appris) : mediane 0.410, vigilance q98 0.591 (risque 30), seuil anomalie 0.635 (risque 50), incident extreme 0.757 (risque 100)
- Decision : 4 fenetres anormales consecutives (8 s) ; fin d'incident apres 5 fenetres normales


### Features directionnelles et jeu d'entrainement

L'Isolation Forest etant symetrique, `vectorize()` ne laisse passer que le sens du danger (hausse de gaz, echauffement, chute d'humidite, correlation gaz-temperature positive) ; les baisses et retours a la normale sont mis a zero (neutre). Le jeu d'entrainement inclut des periodes calmes du matin et de l'apres-midi (apres rebranchement MQ-2 et stabilisation), avec exclusion des essais reels (spray briquet, chauffage DHT22) et des rechauffes post-reboot — voir `training_exclusions.json`.

## 1. Donnees normales (holdout temporel)

| Jeu | Fenetres | Duree | Fenetres > seuil | Incidents (faux positifs) | Episodes 'prediction' | Risque median / p99 |
|---|---|---|---|---|---|---|
| Normal reel | 192 | 0.11 h | 0 (0.00 %) | 0 | 0 | 0.0 / 25.8 |
| Normal + bruit gaz +-10 | 192 | 0.11 h | 0 | 0 | - | - |

## 2. Spray de gaz butane simule (cas de la demo)

20 injections : gaz de ~80 a 200-600 ADC en 2-4 s, plateau 10-30 s, decroissance ~60 s.

- Detectes en `gaz_fumee` en <= 12 s : **20/20** (rappel 100 %)
- Latence de declenchement (debut du spray -> alarme) : mediane 8 s, max 10 s

- Pics isoles d'un seul echantillon (300-1024 ADC, glitch) : **0/20** declenchements (attendu 0)

## 3. Detection precoce (derives lentes) vs regle statique

Comparaison avec ce que ferait une regle statique de type `if gaz > 300` ou `if temp > 40` : on mesure l'avance de l'IA (premiere `prediction` et incident confirme).

| Scenario | Prediction a | Incident a (type) | Regle statique atteinte a | Avance IA |
|---|---|---|---|---|
| Fuite lente +20 ADC/min | 0.7 min | 0.8 min (gaz_fumee) | 11.5 min (gaz > 300) | 10.8 min |
| Fuite lente +8 ADC/min | 0.8 min | non (-) | 28.7 min (gaz > 300) | 27.9 min |
| Echauffement +0.5 degC/min + gaz +2 ADC/min | 0.9 min | 6.2 min (thermique) | 32.0 min (temp > 40 degC) | 31.1 min |
| Echauffement +0.3 degC/min + gaz +1 ADC/min | 0.9 min | 6.7 min (anomalie) | 53.3 min (temp > 40 degC) | 52.4 min |

## 4. Rejeu de l'historique reel (pics de gaz reellement enregistres)

Rejeu en flux de 2865 mesures reelles (11:40 -> 14:49, heure de Paris), incluant les pics de gaz exclus de l'entrainement.

| Debut incident / escalade (Paris) | Type | Gaz brut | Risque |
|---|---|---|---|
| 12:14:34 | gaz_fumee | 186 | 91 |
| 13:55:56 | gaz_fumee | 207 | 74 |
| 14:20:44 | gaz_fumee | 186 | 75 |
| 14:21:36 | gaz_fumee | 1024 | 73 |
| 14:27:20 | thermique | 78 | 58 |
| 14:28:48 | gaz_fumee | 110 | 72 |
| 14:36:18 | gaz_fumee | 105 | 79 |

Episodes `prediction` (pre-alerte sans son) : 10.

## Synthese

- Rappel spray butane (<= 12 s) : 100 %
- Faux incidents sur 0.11 h de donnees normales : 0 (0.00 / h)
- Precision approchee (incidents vrais / incidents leves) : 100 %
- Derives lentes detectees avant la regle statique : 4/4
- Pics isoles (glitch) ignores : 20/20
