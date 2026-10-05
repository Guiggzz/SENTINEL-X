// Melodies d'alarme originales pour buzzer passif (SENTINEL-X)
// Format : frequence (Hz), duree (ms) ; 0 Hz = silence. Jouees en son continu (pas de micro-coupure).
// GAZ    : sirene deux tons 1900/1300 Hz (rapport ~3:2 des sirenes europeennes, transpose
//          dans la zone de resonance du buzzer pour le volume) + 2 balayages montants 1000->2700 Hz.
//          Cycle 2520 ms, boucle jusqu'a buzzer_off ou fin de duration_ms.
// INTRUS : triple bip sec 3100 Hz puis pause. Cycle 780 ms.
#pragma once
const int GAZ_PATTERN[] = {
  1900,230, 1300,230, 1900,230, 1300,230, 1900,230, 1300,230, 1900,230, 1300,230,
  1000,20, 1113,20, 1226,20, 1340,20, 1453,20, 1566,20, 1680,20, 1793,20,
  1906,20, 2020,20, 2133,20, 2246,20, 2360,20, 2473,20, 2586,20, 2700,20,
  1000,20, 1113,20, 1226,20, 1340,20, 1453,20, 1566,20, 1680,20, 1793,20,
  1906,20, 2020,20, 2133,20, 2246,20, 2360,20, 2473,20, 2586,20, 2700,20,
  0,40,
};
const int GAZ_STEPS = sizeof(GAZ_PATTERN) / sizeof(GAZ_PATTERN[0]) / 2;
const int INTRUS_PATTERN[] = {
  3100,90, 0,60, 3100,90, 0,60, 3100,90, 0,60, 0,330,
};
const int INTRUS_STEPS = sizeof(INTRUS_PATTERN) / sizeof(INTRUS_PATTERN[0]) / 2;
